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
    .app-title {
        font-size: 1.75rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
    }
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
    [data-testid="stForm"] button[kind="formSubmit"] {
        display: none !important;
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


def node_passes_submaterial_filter(node: dict[str, Any]) -> bool:
    choice = st.session_state.filter_attr_block
    if choice == "(no filter)":
        return True
    if choice == "(any values)":
        return bool(extract_attribute_rows(node.get("props") or {}))
    return has_attr_block(node.get("props"), choice)


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
    if st.session_state.filter_attr_block == "(no filter)":
        return False
    if not st.session_state.path_ids:
        return False

    current_path = list(st.session_state.path_ids)

    for candidate_id in reversed(current_path):
        candidate_node = indexes["nodes_by_id"].get(candidate_id)
        if not candidate_node:
            continue

        if node_passes_submaterial_filter(candidate_node):
            new_path = path_to_node(indexes, candidate_id)
            if new_path != current_path:
                st.session_state.path_ids = new_path
                return True
            return False

        filtered_descendant_id = first_filtered_descendant(indexes, candidate_id)
        if filtered_descendant_id:
            new_path = path_to_node(indexes, candidate_id)
            if new_path != current_path:
                st.session_state.path_ids = new_path
                return True
            return False

    root_id = current_path[0]
    if current_path != [root_id]:
        st.session_state.path_ids = [root_id]
        return True

    return False


def attr_rows_for_display(node: dict[str, Any]) -> list[dict[str, str]]:
    return extract_attribute_rows(node.get("props") or {})

#compare checkbox
def part_compare_key(material_id: str, attribute: str) -> str:
    return f"{material_id}|{attribute}"


def is_part_in_compare(material_id: str, attribute: str) -> bool:
    return any(
        p["key"] == part_compare_key(material_id, attribute)
        for p in st.session_state.compare_parts
    )

def add_part_to_compare(
    material_id: str, material_name: str, attribute: str, value: str
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


def is_material_in_compare(material_id: str) -> bool:
    return any(m["id"] == material_id for m in st.session_state.compare_materials)


def add_material_to_compare(material_id: str, material_name: str) -> None:
    if not is_material_in_compare(material_id):
        st.session_state.compare_materials.append(
            {"id": material_id, "name": material_name}
        )


def remove_material_from_compare(material_id: str) -> None:
    st.session_state.compare_materials = [
        m for m in st.session_state.compare_materials if m["id"] != material_id
    ]

def on_compare_toggle(material_id: str, material_name: str, widget_key: str) -> None:
    if st.session_state[widget_key]:
        add_material_to_compare(material_id, material_name)
        st.session_state.show_compare_view = True
    else:
        remove_material_from_compare(material_id)
        if not st.session_state.compare_materials:
            st.session_state.show_compare_view = False

def grouped_attr_rows_for_display(node: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in attr_rows_for_display(node):
        top_key = row["attribute"].split(".", 1)[0]
        grouped[top_key].append(row)
    return dict(sorted(grouped.items()))

def on_attr_group_toggle(material_id: str, group_name: str) -> None:
    state_key = f"open_attr_group_{material_id}"
    st.session_state[state_key] = (
        None if st.session_state.get(state_key) == group_name else group_name
    )

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
    #end compare box

def on_nav_child(child_id: str) -> None:
    indexes = st.session_state.get("root_indexes")
    if not indexes or not st.session_state.path_ids:
        return
    current_id = st.session_state.path_ids[-1]
    children = indexes["children_by_parent"].get(current_id, [])
    if any(c["id"] == child_id for c in children):
        st.session_state.path_ids.append(child_id)


def render_current_node_detail(
    node: dict[str, Any],
    indexes: dict[str, Any],
    *,
    level_index: int = 0,
) -> None:
    name = node_name(node)
    attr_rows = attr_rows_for_display(node)
    all_children = indexes["children_by_parent"].get(node["id"], [])
    children = filter_nodes_by_attr(all_children)

    title = name
    if children:
        title += f"  ({len(children)} submaterials)"
    if attr_rows:
        title += f"  [{len(attr_rows)} values]"

    with st.expander(title, expanded=True):
        # attr display
        attr_groups = grouped_attr_rows_for_display(pn)

        if attr_groups:
            open_group = st.session_state.get(f"open_attr_group_{pn['id']}")
            for group_name, group_rows in attr_groups.items():
                st.button(
                    f"{group_name} [{len(group_rows)} values]",
                    key=f"attr_group_{pn['id']}_{group_name}_{i}",
                    on_click=on_attr_group_toggle,
                    args=(pn["id"], group_name),
                    use_container_width=True,
                )
                if open_group == group_name:
                    st.dataframe(
                        pd.DataFrame(group_rows),
                        use_container_width=True,
                        hide_index=True,
                        height=min(38 + 28 * len(group_rows), 280),
                    )
        else:
            st.caption("No attribute values on this node.")
            #end

        st.session_state.show_compare_view = st.checkbox(
            "Compare",
            value=st.session_state.show_compare_view,
            key=f"cmp_{node['id']}_{level_index}",
        )
        cb_key = f"bill_{node['id']}_path_{level_index}"
        st.checkbox(
            "Add to bill of materials",
            value=is_in_bill(node["id"]),
            key=cb_key,
            on_change=on_bill_toggle,
            args=(node["id"], cb_key),
        )

    if children:
        st.markdown("**Submaterials**")
        for child in children:
            cname = node_name(child)
            n_child = len(indexes["children_by_parent"].get(child["id"], []))
            label = cname
            if n_child:
                label += f" ({n_child} submaterials)"
            n_vals = len(attr_rows_for_display(child))
            if n_vals:
                label += f"  [{n_vals} values]"
            st.button(
                label,
                key=f"nav_{node['id']}_{child['id']}_{level_index}",
                on_click=on_nav_child,
                args=(child["id"],),
                use_container_width=True,
            )
    elif all_children:
        st.caption("No submaterials match the current filter.")
    else:
        st.caption("No submaterials here.")
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
def on_crumb_click(idx: int) -> None:
    if st.session_state.path_ids and 0 <= idx < len(st.session_state.path_ids):
        st.session_state.path_ids = st.session_state.path_ids[: idx + 1]


def render_clickable_path(path_ids: list[str], indexes: dict[str, Any]) -> None:
    labels = get_path_labels_from_indexes(path_ids, indexes["nodes_by_id"])
    if not labels:
        return

    st.caption("Path")
    for i, label in enumerate(labels):
        st.button(
            label,
            key=f"crumb_{path_ids[i]}_{i}",
            on_click=on_crumb_click,
            args=(i,),
            use_container_width=True,
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

if "compare_materials" not in st.session_state:
    st.session_state.compare_materials = []

if "show_compare_view" not in st.session_state:
    st.session_state.show_compare_view = False

# =============================================================================
# SECTION 11 — APP STARTUP
# =============================================================================
st.markdown(
    '<p class="app-title">Material Ontology Explorer</p>',
    unsafe_allow_html=True,
)

# =============================================================================
# SECTION 12 — SIDEBAR
# =============================================================================
with st.sidebar:
    st.header("Navigation")

    #roo dropdown
    roots = get_root_nodes()
    root_map = {r["id"]: r["label"] for r in roots}
    browse_options = [""] + list(root_map.keys())

    if "browse_root" not in st.session_state:
        st.session_state.browse_root = ""

    if st.session_state.path_ids:
        current_root_id = st.session_state.path_ids[0]
        if current_root_id in root_map:
            st.session_state.browse_root = current_root_id

    browse_pick = st.selectbox(
        "Top level",
        options=browse_options,
        index=browse_options.index(st.session_state.browse_root)
        if st.session_state.browse_root in browse_options
        else 0,
        format_func=lambda rid: "— select —" if rid == "" else root_map[rid],
    )

    if browse_pick != st.session_state.browse_root:
        st.session_state.browse_root = browse_pick
        if browse_pick:
            st.session_state.has_searched = True
            st.session_state.path_ids = [browse_pick]
            st.session_state.root_indexes = None
            st.session_state.search_feedback = ""
        st.rerun()
        #end dropdoen roots

    with st.form("global_material_search", clear_on_submit=False):
        search_query = st.text_input("query", placeholder="", label_visibility="collapsed")
        search_submitted = st.form_submit_button("Search")

    if search_submitted:
        found_path_ids = search_material_path(search_query)
        if found_path_ids:
            st.session_state.has_searched = True
            st.session_state.path_ids = found_path_ids
            st.session_state.root_indexes = None
            st.session_state.search_feedback = ""
            st.session_state.browse_root = "— select —"
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
    
    if st.session_state.has_searched and st.session_state.path_ids:
        root_id = st.session_state.path_ids[0]
        root_rows = fetch_root_subtree(root_id)
        indexes = build_subtree_indexes(root_rows, root_id)
        st.session_state.root_indexes = indexes
        if apply_filter_auto_dive(indexes):
            st.rerun()
        render_clickable_path(st.session_state.path_ids, indexes)
        
    #compare list
   if st.session_state.compare_materials:
    st.divider()
    st.caption("Compare list")
    for m in st.session_state.compare_materials:
        st.caption(f"• {m['name']}")
    if st.button("Clear compare list", use_container_width=True):
        st.session_state.compare_materials = []
        st.session_state.compare_parts = []
        st.session_state.show_compare_view = False
        st.rerun()
    #end compare list
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


# =============================================================================
# SECTION 14 — MAIN TABS
# =============================================================================
tab_path, tab_table, tab_bom = st.tabs(
    ["Path + explore", "All values (table)", "Pick for BOM"])

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

    for i, pn in enumerate(path_nodes):
        is_current = i == len(path_nodes) - 1
        name = node_name(pn)
        attr_rows = attr_rows_for_display(pn)
        all_children = indexes["children_by_parent"].get(pn["id"], [])

        title = name
        if all_children:
            title += f"  ({len(all_children)} submaterials)"
        if attr_rows:
            title += f"  [{len(attr_rows)} values]"

        with st.expander(title, expanded=is_current):
            if attr_rows:
                st.dataframe(
                    pd.DataFrame(attr_rows),
                    use_container_width=True,
                    hide_index=True,
                    height=min(38 + 28 * len(attr_rows), 280),
                )
            else:
                st.caption("No attribute values on this node.")
            #if current display
            cmp_key = f"cmp_{pn['id']}_{i}"
                st.checkbox(
                    "Compare",
                    value=is_material_in_compare(pn["id"]),
                    key=cmp_key,
                    on_change=on_compare_toggle,
                    args=(pn["id"], name, cmp_key),
                )
                cb_key = f"bill_{pn['id']}_path_{i}"
                st.checkbox(
                    "Add to bill of materials",
                    value=is_in_bill(pn["id"]),
                    key=cb_key,
                    on_change=on_bill_toggle,
                    args=(pn["id"], cb_key),
                )
                #end if current display
    
    current = path_nodes[-1]
    children = filter_nodes_by_attr(
        indexes["children_by_parent"].get(current["id"], [])
    )

    st.markdown("**Submaterials**")
    if not children:
        st.caption("No submaterials here.")
    else:
        for child in children:
            cname = node_name(child)
            n_child = len(indexes["children_by_parent"].get(child["id"], []))
            label = cname
            if n_child:
                label += f" ({n_child} submaterials)"
            n_vals = len(attr_rows_for_display(child))
            if n_vals:
                label += f"  [{n_vals} values]"
            st.button(
                label,
                key=f"nav_{current['id']}_{child['id']}",
                on_click=on_nav_child,
                args=(child["id"],),
                use_container_width=True,
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
