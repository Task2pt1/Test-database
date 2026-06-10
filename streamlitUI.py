# streamlitUI.py
# Neo4j -> global search + cached subtree fetch -> Streamlit UI

# =============================================================================
# SECTION 1 — SETUP
# =============================================================================
from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any

import pandas as pd
import streamlit as st
from neo4j import Driver, GraphDatabase

st.set_page_config(page_title="Material Ontology Explorer", layout="wide")

NODE_LABEL = "Material"
CHILD_REL = "HAS_CHILD"

ATTR_BLOCKS = (
    "engineering",
    "activity",
    "lcia",
    "material_cost",
    "standards",
    "synonyms",
    "citation",
    "comment",
    "notes",
    "region",
)

META_KEYS = {"name", "id", "code", "database", "vector", "placement"}

FILTER_ATTR_OPTIONS = ["(no filter)", "(any values)", *ATTR_BLOCKS]


# =============================================================================
# SECTION 2 — CSS
# =============================================================================
st.markdown(
    """
    <style>
    .crumbs {
        font-size: 0.95rem;
        line-height: 1.6;
        margin: 0.25rem 0 0.75rem 0;
        word-wrap: break-word;
    }
    .crumbs a {
        color: inherit;
        text-decoration: none;
        font-weight: 500;
        margin-right: 0.15rem;
    }
    .crumbs a:hover {
        text-decoration: underline;
    }
    .crumb-sep {
        opacity: 0.6;
        margin: 0 0.25rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# SECTION 3 — NEO4J CONNECTION
# =============================================================================
@st.cache_resource
def get_driver() -> Driver:
    return GraphDatabase.driver(
        st.secrets["NEO4J_URI"].strip(),
        auth=(
            st.secrets["NEO4J_USERNAME"].strip(),
            st.secrets["NEO4J_PASSWORD"].strip(),
        ),
    )


driver = get_driver()


def run_query(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with driver.session() as session:
        return [r.data() for r in session.run(query, params or {})]


# =============================================================================
# SECTION 4 — PROPERTY PARSING
# =============================================================================
def parse_stored(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        if s and s[0] in "{[":
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                pass
    return v


def parse_props(props: dict[str, Any] | None) -> dict[str, Any]:
    return {k: parse_stored(v) for k, v in (props or {}).items()}


def flatten_leaves(obj: Any, prefix: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            rows.extend(flatten_leaves(v, path))
    elif isinstance(obj, list):
        if obj and all(not isinstance(x, (dict, list)) for x in obj):
            rows.append({"attribute": prefix, "value": ", ".join(str(x) for x in obj)})
        else:
            for i, item in enumerate(obj):
                rows.extend(flatten_leaves(item, f"{prefix}[{i}]"))
    elif obj not in (None, ""):
        rows.append({"attribute": prefix, "value": str(obj)})

    return rows


def extract_attribute_rows(props: dict[str, Any]) -> list[dict[str, str]]:
    parsed = parse_props(props)
    rows: list[dict[str, str]] = []

    for key in ATTR_BLOCKS:
        if key not in parsed or parsed[key] in (None, "", {}, []):
            continue
        rows.extend(flatten_leaves(parsed[key], key))

    for k, v in parsed.items():
        if k in META_KEYS or k in ATTR_BLOCKS:
            continue
        rows.extend(flatten_leaves(v, k))

    return rows


def attrs_to_wide_row(attr_rows: list[dict[str, str]]) -> dict[str, str]:
    return {r["attribute"]: r["value"] for r in attr_rows}


def node_name(node: dict[str, Any]) -> str:
    props = parse_props(node.get("props"))
    return props.get("name") or node.get("label") or node.get("id") or "Unknown"


# =============================================================================
# SECTION 5 — NEO4J FETCHES
# =============================================================================
def get_root_nodes() -> list[dict[str, str]]:
    return run_query(
        f"""
        MATCH (n:{NODE_LABEL})
        WHERE NOT ()-[:{CHILD_REL}]->(n)
        RETURN n.id AS id, n.name AS label
        ORDER BY label
        """
    )


@st.cache_data(show_spinner=False)
def fetch_root_subtree(root_id: str) -> list[dict[str, Any]]:
    return run_query(
        f"""
        MATCH (root:{NODE_LABEL} {{id: $root_id}})
        OPTIONAL MATCH p = (root)-[:{CHILD_REL}*0..]->(n:{NODE_LABEL})
        WITH root, n, min(length(p)) AS depth
        OPTIONAL MATCH (parent:{NODE_LABEL})-[:{CHILD_REL}]->(n)
        WHERE parent IS NULL
           OR parent = root
           OR (root)-[:{CHILD_REL}*1..]->(parent)
        RETURN
            n.id AS id,
            n.name AS label,
            properties(n) AS props,
            depth,
            head([x IN collect(parent.id) WHERE x IS NOT NULL]) AS parent_id
        ORDER BY depth, label
        """,
        {"root_id": root_id},
    )


def search_material_path(query: str) -> list[str]:
    q = query.strip().lower()
    if not q:
        return []

    rows = run_query(
        f"""
        MATCH (n:{NODE_LABEL})
        WHERE toLower(coalesce(n.name, '')) CONTAINS $q
           OR toLower(coalesce(n.id, '')) CONTAINS $q
           OR toLower(coalesce(n.code, '')) CONTAINS $q

        OPTIONAL MATCH p = (root:{NODE_LABEL})-[:{CHILD_REL}*0..]->(n)
        WHERE NOT ()-[:{CHILD_REL}]->(root)

        WITH n, p,
             CASE
                 WHEN toLower(coalesce(n.name, '')) = $q THEN 0
                 WHEN toLower(coalesce(n.id, '')) = $q THEN 1
                 WHEN toLower(coalesce(n.code, '')) = $q THEN 2
                 WHEN toLower(coalesce(n.name, '')) STARTS WITH $q THEN 3
                 WHEN toLower(coalesce(n.id, '')) STARTS WITH $q THEN 4
                 WHEN toLower(coalesce(n.code, '')) STARTS WITH $q THEN 5
                 ELSE 6
             END AS rank_score

        WITH n, p, rank_score
        ORDER BY rank_score, length(p), n.name
        LIMIT 1

        RETURN [x IN nodes(p) | x.id] AS path_ids
        """,
        {"q": q},
    )

    if not rows:
        return []

    return rows[0].get("path_ids") or []


# =============================================================================
# SECTION 6 — IN-MEMORY INDEXES
# =============================================================================
def build_subtree_indexes(rows: list[dict[str, Any]], root_id: str) -> dict[str, Any]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    parent_by_id: dict[str, str | None] = {}
    depth_by_id: dict[str, int] = {}

    for row in rows:
        node = {
            "id": row["id"],
            "label": row["label"],
            "props": row["props"],
            "depth": row["depth"],
            "parent_id": row["parent_id"],
        }
        nodes_by_id[row["id"]] = node
        parent_by_id[row["id"]] = row["parent_id"]
        depth_by_id[row["id"]] = row["depth"]

    for row in rows:
        parent_id = row["parent_id"]
        if parent_id is not None:
            children_by_parent[parent_id].append(nodes_by_id[row["id"]])

    for _, children in children_by_parent.items():
        children.sort(key=node_name)

    descendants_by_id: dict[str, list[str]] = {}
    for node_id in nodes_by_id:
        out: list[str] = []
        queue = deque(child["id"] for child in children_by_parent.get(node_id, []))
        while queue:
            current = queue.popleft()
            out.append(current)
            queue.extend(child["id"] for child in children_by_parent.get(current, []))
        descendants_by_id[node_id] = out

    root_node = nodes_by_id[root_id]
    root_name = node_name(root_node)

    return {
        "root_id": root_id,
        "root_name": root_name,
        "rows": rows,
        "nodes_by_id": nodes_by_id,
        "children_by_parent": children_by_parent,
        "parent_by_id": parent_by_id,
        "depth_by_id": depth_by_id,
        "descendants_by_id": descendants_by_id,
    }


def get_path_labels_from_indexes(
    path_ids: list[str],
    nodes_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    return [node_name(nodes_by_id[node_id]) for node_id in path_ids if node_id in nodes_by_id]


def get_subtree_rows_from_indexes(node_id: str, indexes: dict[str, Any]) -> list[dict[str, Any]]:
    ids = [node_id] + indexes["descendants_by_id"].get(node_id, [])
    rows: list[dict[str, Any]] = []

    for descendant_id in ids:
        node = indexes["nodes_by_id"][descendant_id]
        rows.append(
            {
                "id": node["id"],
                "label": node["label"],
                "props": node["props"],
                "depth": node["depth"] - indexes["depth_by_id"][node_id],
            }
        )

    rows.sort(key=lambda r: (r["depth"], node_name(r)))
    return rows


# =============================================================================
# SECTION 7 — FILTER, COMPARE, AND CURRENT-NODE DISPLAY
# =============================================================================
def has_attr_block(props: dict[str, Any] | None, block: str) -> bool:
    parsed = parse_props(props)
    val = parsed.get(block)
    return val not in (None, "", {}, [])


def has_any_attribute_values(props: dict[str, Any] | None) -> bool:
    return bool(extract_attribute_rows(props or {}))


def node_passes_submaterial_filter(node: dict[str, Any]) -> bool:
    choice = st.session_state.filter_attr_block
    if choice == "(no filter)":
        return True
    if choice == "(any values)":
        return has_any_attribute_values(node.get("props"))
    rows = extract_attribute_rows(node.get("props") or {})
    return any(
        r["attribute"] == choice or r["attribute"].startswith(f"{choice}.")
        for r in rows)


def filter_nodes_by_attr(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in nodes if node_passes_submaterial_filter(n)]

def path_to_node(indexes: dict[str, Any], target_id: str) -> list[str]:
    parent_by_id = indexes["parent_by_id"]
    chain: list[str] = []
    cur: str | None = target_id
    while cur is not None:
        chain.append(cur)
        cur = parent_by_id.get(cur)
    return list(reversed(chain))


def first_filtered_descendant(indexes: dict[str, Any], start_id: str) -> str | None:
    """First descendant under start_id that passes the sidebar filter (BFS)."""
    start_node = indexes["nodes_by_id"].get(start_id)
    if not start_node:
        return None

    if node_passes_submaterial_filter(start_node):
        return start_id

    queue = deque([start_id])
    while queue:
        nid = queue.popleft()
        for child in indexes["children_by_parent"].get(nid, []):
            if node_passes_submaterial_filter(child):
                return child["id"]
            queue.append(child["id"])
    return None


def apply_filter_auto_dive(indexes: dict[str, Any]) -> bool:
    """From current search node, jump to first matching child. True if path changed."""
    if st.session_state.filter_attr_block == "(no filter)":
        return False
    if not st.session_state.path_ids:
        return False

    anchor_id = st.session_state.path_ids[-1]
    anchor_node = indexes["nodes_by_id"].get(anchor_id)
    if not anchor_node:
        return False

    if node_passes_submaterial_filter(anchor_node):
        return False

    target_id = first_filtered_descendant(indexes, anchor_id)
    if not target_id or target_id == anchor_id:
        return False

    full = path_to_node(indexes, target_id)
    root = st.session_state.path_ids[0]
    new_path = full[full.index(root) :] if root in full else full

    if new_path == st.session_state.path_ids:
        return False

    st.session_state.path_ids = new_path
    return True

def all_filtered_descendants(
    indexes: dict[str, Any], start_id: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    queue = deque([start_id])
    while queue:
        nid = queue.popleft()
        node = indexes["nodes_by_id"][nid]
        if nid != start_id and node_passes_submaterial_filter(node):
            out.append(node)
        for child in indexes["children_by_parent"].get(nid, []):
            queue.append(child["id"])
    out.sort(key=node_name)
    return out

def apply_filter_auto_dive(indexes: dict[str, Any]) -> bool:
    """Dive from current search node into first matching descendant. Returns True if path changed."""
    if st.session_state.filter_attr_block == "(no filter)":
        return False
    if not st.session_state.path_ids:
        return False
    anchor_id = st.session_state.path_ids[-1]
    anchor_node = indexes["nodes_by_id"].get(anchor_id)
    if not anchor_node:
        return False
    if node_passes_submaterial_filter(anchor_node):
        return False
    target_id = first_filtered_descendant(indexes, anchor_id)
    if not target_id or target_id == anchor_id:
        return False
    full = path_to_node(indexes, target_id)
    root = st.session_state.path_ids[0]
    new_path = full[full.index(root) :] if root in full else full
    if new_path == st.session_state.path_ids:
        return False
    st.session_state.path_ids = new_path
    return True

def attr_rows_for_display(node: dict[str, Any]) -> list[dict[str, str]]:
    return extract_attribute_rows(node.get("props") or {})
    
def node_passes_submaterial_filter(node: dict[str, Any]) -> bool:
    choice = st.session_state.filter_attr_block
    if choice == "(no filter)":
        return True
    if choice == "(any values)":
        return bool(extract_attribute_rows(node.get("props") or {}))
    return has_attr_block(node.get("props"), choice)

def part_compare_key(material_id: str, attribute: str) -> str:
    return f"{material_id}|{attribute}"


def is_part_in_compare(material_id: str, attribute: str) -> bool:
    k = part_compare_key(material_id, attribute)
    return any(p["key"] == k for p in st.session_state.compare_parts)


def add_part_to_compare(
    material_id: str,
    material_name: str,
    attribute: str,
    value: str,
) -> None:
    entry = {
        "key": part_compare_key(material_id, attribute),
        "material_id": material_id,
        "material_name": material_name,
        "attribute": attribute,
        "value": value,
    }
    if not any(p["key"] == entry["key"] for p in st.session_state.compare_parts):
        st.session_state.compare_parts.append(entry)


def remove_part_from_compare(key: str) -> None:
    st.session_state.compare_parts = [
        p for p in st.session_state.compare_parts if p["key"] != key
    ]


def add_block_to_compare(node: dict[str, Any], block_key: str) -> None:
    props = parse_props(node.get("props"))
    if block_key not in props:
        return
    name = node_name(node)
    rows = flatten_leaves(props[block_key], block_key)
    for r in rows:
        add_part_to_compare(node["id"], name, r["attribute"], r["value"])


def render_parts_compare(parts: list[dict[str, str]]) -> None:
    if len(parts) < 2:
        st.caption("Add at least 2 parts to compare (use + on any field).")
        return

    cols = st.columns(len(parts))
    for col, part in zip(cols, parts):
        with col:
            st.markdown(f"**{part['material_name']}**")
            st.caption(part["attribute"])
            st.write(part["value"])

def render_current_node_detail(
    node: dict[str, Any],
    indexes: dict[str, Any],
    *,
    level_index: int = 0,
) -> None:
    props = parse_props(node.get("props"))
    name = node_name(node)
    attr_rows = attr_rows_for_display(node)
    children = indexes["children_by_parent"].get(node["id"], [])
    children = filter_nodes_by_attr(children)
    choice = st.session_state.filter_attr_block

    if attr_rows:
        st.markdown("**Attribute values**")
        for r in attr_rows:
            c1, c2 = st.columns([6, 1])
            with c1:
                st.text(f"{r['attribute']}: {r['value']}")
            with c2:
                if is_part_in_compare(node["id"], r["attribute"]):
                    if st.button("−", key=f"rm_{node['id']}_{r['attribute']}"):
                        remove_part_from_compare(
                            part_compare_key(node["id"], r["attribute"])
                        )
                        st.rerun()
                else:
                    if st.button("+", key=f"add_{node['id']}_{r['attribute']}"):
                        add_part_to_compare(
                            node["id"], name, r["attribute"], r["value"]
                        )
                        st.rerun()
    else:
        st.caption("No attribute values on this level.")

    for key in ATTR_BLOCKS:
        if key in props and props[key] not in (None, "", {}, []):
            with st.expander(
                f"{key} (structured)",
                key=f"detail_{node['id']}_{level_index}_{key}",
            ):
                val = props[key]
                if isinstance(val, (dict, list)):
                    st.json(val)
                else:
                    st.write(val)
                if st.button(
                    f"Add all {key} fields to compare",
                    key=f"addblock_{node['id']}_{level_index}_{key}",
                ):
                    add_block_to_compare(node, key)
                    st.rerun()

    cb_key = f"bill_{node['id']}_path_{level_index}"
    st.checkbox(
        "Add to bill of materials",
        value=is_in_bill(node["id"]),
        key=cb_key,
        on_change=on_bill_toggle,
        args=(node["id"], cb_key),
    )

    st.markdown("**Submaterials**")
    if not children:
        st.caption("No submaterials here.")
    else:
        for child in children:
            cname = node_name(child)
            n_child = len(indexes["children_by_parent"].get(child["id"], []))
            suffix = f" ({n_child} submaterials)" if n_child else ""
            if choice not in ("(no filter)", "(any values)") and has_attr_block(
                child.get("props"), choice
            ):
                suffix += f" · {choice}"
            st.markdown(
                f'<a href="?nav={child["id"]}" target="_self">{html_escape(cname)}</a>'
                f"{html_escape(suffix)}",
                unsafe_allow_html=True,
            )


# =============================================================================
# SECTION 8 — BOM HELPERS
# =============================================================================
def is_in_bill(material_id: str) -> bool:
    for items in st.session_state.bom.values():
        if any(b["id"] == material_id for b in items):
            return True
    return False


def add_to_bill_from_node(node: dict[str, Any], category: str) -> None:
    attr_rows = extract_attribute_rows(node.get("props") or {})
    entry = {
        "id": node["id"],
        "name": node_name(node),
        "values": attrs_to_wide_row(attr_rows),
    }
    st.session_state.bom.setdefault(category, [])
    if not any(b["id"] == node["id"] for b in st.session_state.bom[category]):
        st.session_state.bom[category].append(entry)


def remove_from_bill(material_id: str) -> None:
    for cat, items in list(st.session_state.bom.items()):
        st.session_state.bom[cat] = [b for b in items if b["id"] != material_id]
        if not st.session_state.bom[cat]:
            del st.session_state.bom[cat]


def on_bill_toggle(material_id: str, widget_key: str) -> None:
    indexes = st.session_state.get("root_indexes")
    if not indexes:
        return

    if st.session_state[widget_key]:
        node = indexes["nodes_by_id"].get(material_id)
        if node:
            add_to_bill_from_node(node, indexes["root_name"])
    else:
        remove_from_bill(material_id)


# =============================================================================
# SECTION 9 — NAVIGATION
# =============================================================================
def handle_breadcrumb_click() -> None:
    crumb_index = st.query_params.get("crumb")
    if crumb_index is None:
        return

    try:
        idx = int(str(crumb_index))
    except (TypeError, ValueError):
        st.query_params.clear()
        return

    if st.session_state.path_ids and 0 <= idx < len(st.session_state.path_ids):
        st.session_state.path_ids = st.session_state.path_ids[: idx + 1]

    st.query_params.clear()


def handle_child_nav_click() -> None:
    nav_id = st.query_params.get("nav")
    if not nav_id:
        return

    indexes = st.session_state.get("root_indexes")
    if not indexes or not st.session_state.path_ids:
        st.query_params.clear()
        return

    current_id = st.session_state.path_ids[-1]
    children = indexes["children_by_parent"].get(current_id, [])
    if any(c["id"] == nav_id for c in children):
        st.session_state.path_ids.append(nav_id)

    st.query_params.clear()


def render_clickable_path(path_ids: list[str], indexes: dict[str, Any]) -> None:
    labels = get_path_labels_from_indexes(path_ids, indexes["nodes_by_id"])
    if not labels:
        return

    st.caption("Path")
    parts: list[str] = []
    for i, label in enumerate(labels):
        safe_label = html_escape(label)
        parts.append(f'<a href="?crumb={i}" target="_self">{safe_label}</a>')

    separator = '<span class="crumb-sep">›</span>'
    st.markdown(
        f'<div class="crumbs">{separator.join(parts)}</div>',
        unsafe_allow_html=True,
    )


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


# =============================================================================
# SECTION 10 — SESSION STATE
# =============================================================================
if "has_searched" not in st.session_state:
    st.session_state.has_searched = False
    st.session_state.path_ids = []
    st.session_state.root_indexes = None
    st.session_state.search_feedback = ""

if "bom" not in st.session_state:
    st.session_state.bom = {}

if "filter_attr_block" not in st.session_state:
    st.session_state.filter_attr_block = "(no filter)"

if "compare_parts" not in st.session_state:
    st.session_state.compare_parts = []

if "show_compare_view" not in st.session_state:
    st.session_state.show_compare_view = False


# =============================================================================
# SECTION 11 — APP STARTUP
# =============================================================================
handle_breadcrumb_click()
handle_child_nav_click()

st.title("Material Ontology Explorer")


# =============================================================================
# SECTION 12 — SIDEBAR
# =============================================================================
with st.sidebar:
    st.header("Navigation")

    with st.form("global_material_search", clear_on_submit=False):
        search_query = st.text_input(
            "Search",
            placeholder="Search by name, id, or code",
        )
        search_submitted = st.form_submit_button("Search", use_container_width=True)

    if search_submitted:
        found_path_ids = search_material_path(search_query)
        if found_path_ids:
            st.session_state.has_searched = True
            st.session_state.path_ids = found_path_ids
            st.session_state.root_indexes = None
            st.session_state.search_feedback = ""
            st.rerun()
        else:
            st.session_state.search_feedback = "No material found."

    if st.session_state.search_feedback:
        st.caption(st.session_state.search_feedback)

    st.session_state.filter_attr_block = st.selectbox(
        "Only show submaterials with:",
        options=FILTER_ATTR_OPTIONS,
        index=FILTER_ATTR_OPTIONS.index(st.session_state.filter_attr_block)
        if st.session_state.filter_attr_block in FILTER_ATTR_OPTIONS
        else 0,
    )

    st.divider()
    st.subheader("Compare")

    if st.session_state.compare_parts:
        for p in st.session_state.compare_parts:
            st.caption(f"• {p['material_name']} — {p['attribute']}")

    st.session_state.show_compare_view = st.checkbox(
        "Show side-by-side comparison",
        value=st.session_state.show_compare_view,
    )

    if st.button("Clear compare list", use_container_width=True):
        st.session_state.compare_parts = []
        st.rerun()

    if st.session_state.has_searched and st.session_state.path_ids:
        root_id = st.session_state.path_ids[0]
        root_rows = fetch_root_subtree(root_id)
        indexes = build_subtree_indexes(root_rows, root_id)
        st.session_state.root_indexes = indexes
        if apply_filter_auto_dive(indexes):
            st.rerun()
        render_clickable_path(st.session_state.path_ids, indexes)

    st.divider()
    st.subheader("Bill of materials")
    if not st.session_state.bom:
        st.caption("Empty.")
    else:
        for cat in sorted(st.session_state.bom.keys()):
            st.markdown(f"**{cat}**")
            for i, item in enumerate(st.session_state.bom[cat], 1):
                st.write(f"{i}. {item['name']}")
                vals = item.get("values") or {}
                if vals:
                    preview = "; ".join(
                        f"{k}={v}" for k, v in list(vals.items())[:3]
                    )
                    if len(vals) > 3:
                        preview += f" … (+{len(vals) - 3} more)"
                    st.caption(preview)

    if st.button("Clear bill", use_container_width=True):
        st.session_state.bom = {}
        st.rerun()


# =============================================================================
# SECTION 13 — MAIN AREA GATE
# =============================================================================
if not st.session_state.has_searched or not st.session_state.path_ids:
    st.info("Search for a material by name, id, or code to get started.")
    st.stop()

if not st.session_state.root_indexes:
    st.info("Loading material data…")
    st.stop()

indexes = st.session_state.root_indexes
current_id = st.session_state.path_ids[-1]
node = indexes["nodes_by_id"].get(current_id)

if not node:
    st.error("Could not load this material.")
    st.stop()

direct_children = indexes["children_by_parent"].get(current_id, [])
subtree = get_subtree_rows_from_indexes(current_id, indexes)

st.header(node_name(node))
st.caption(
    f"Direct submaterials: **{len(direct_children)}** · "
    f"Total nodes in subtree: **{len(subtree)}**"
)


# =============================================================================
# SECTION 14 — MAIN TABS
# =============================================================================
tab_path, tab_table, tab_bom = st.tabs(
    ["Path + explore", "All values (table)", "Pick for BOM"]
)


# --- TAB 1 ---
with tab_path:
    path_nodes = [
        indexes["nodes_by_id"][nid]
        for nid in st.session_state.path_ids
        if nid in indexes["nodes_by_id"]
    ]
    path_labels = [node_name(pn) for pn in path_nodes]
    if path_labels:
        st.subheader(" › ".join(path_labels))
    else:
        st.subheader("Explore")
    if st.session_state.show_compare_view and len(st.session_state.compare_parts) >= 2:
        st.markdown("**Comparison**")
        render_parts_compare(st.session_state.compare_parts)
        st.divider()
    render_current_node_detail(
        path_nodes[-1], indexes, level_index=len(path_nodes) - 1
    )

# --- TAB 2 ---
with tab_table:
    st.subheader("All materials under this node — every extracted value")
    all_rows: list[dict[str, str]] = []

    for row in subtree:
        p = parse_props(row["props"])
        mat_name = p.get("name") or row["label"]
        depth = row["depth"]
        for attr_row in extract_attribute_rows(row["props"] or {}):
            all_rows.append(
                {
                    "depth": depth,
                    "material": mat_name,
                    "attribute": attr_row["attribute"],
                    "value": attr_row["value"],
                    "_id": row["id"],
                }
            )

    if not all_rows:
        st.info("No attribute values found in this subtree.")
    else:
        df = pd.DataFrame(all_rows)

        pick = st.data_editor(
            df.assign(compare=False),
            column_config={"compare": st.column_config.CheckboxColumn("Compare")},
            disabled=[c for c in df.columns if c != "compare"],
            hide_index=True,
            use_container_width=True,
            key="table_compare_pick",
        )

        if st.button("Add checked rows to compare"):
            for i, row in pick.iterrows():
                if not row.get("compare"):
                    continue
                add_part_to_compare(
                    df.loc[i, "_id"],
                    row["material"],
                    row["attribute"],
                    row["value"],
                )
            st.rerun()

        st.download_button(
            "Download extracted values (CSV)",
            df.drop(columns=["_id"]).to_csv(index=False),
            file_name=f"{node_name(node)}_extract.csv",
            mime="text/csv",
        )


# --- TAB 3 ---
with tab_bom:
    st.subheader("Pick materials")
    scope = st.radio(
        "List",
        ["Children here", "All under this node"],
        horizontal=True,
    )

    if scope == "Children here":
        items = direct_children
    else:
        item_ids = indexes["descendants_by_id"].get(current_id, [])
        items = [indexes["nodes_by_id"][item_id] for item_id in item_ids]

    table_rows = []
    for x in items:
        attr_rows = extract_attribute_rows(x["props"] or {})
        summary = "; ".join(
            f"{r['attribute']}={r['value']}" for r in attr_rows[:4]
        )
        if len(attr_rows) > 4:
            summary += f" … (+{len(attr_rows) - 4} more)"

        table_rows.append(
            {
                "bill": False,
                "name": node_name(x),
                "values": summary or "—",
                "_id": x["id"],
            }
        )

    if not table_rows:
        st.caption("Nothing to pick.")
    else:
        df = pd.DataFrame(table_rows)
        edited = st.data_editor(
            df.drop(columns=["_id"]),
            column_config={"bill": st.column_config.CheckboxColumn("Bill")},
            disabled=[c for c in df.columns if c not in ("bill",)],
            hide_index=True,
            use_container_width=True,
            key="pick_editor",
        )

        if st.button("Add checked to bill"):
            for i, row in edited.iterrows():
                if not row.get("bill"):
                    continue
                mid = df.loc[i, "_id"]
                selected_node = indexes["nodes_by_id"][mid]
                add_to_bill_from_node(selected_node, indexes["root_name"])
            st.rerun()
