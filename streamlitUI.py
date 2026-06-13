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

FILTER_ATTR_OPTIONS = ["(no filter)",  *ATTR_BLOCKS]


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


    .compare-scroll {
        overflow-x: auto;
        overflow-y: auto;
        max-width: 100%;
        border: 1px solid rgba(250, 250, 250, 0.10);
        border-radius: 12px;
    }

    .compare-table {
        border-collapse: collapse;
        table-layout: fixed;
        width: max-content;
        min-width: 100%;
        font-size: 0.88rem;
    }

    .compare-table th,
    .compare-table td {
        border: 1px solid rgba(250, 250, 250, 0.08);
        padding: 8px 10px;
        text-align: left;
        vertical-align: top;
        white-space: normal;
        word-break: break-word;
        overflow-wrap: anywhere;
    }

    .compare-table th {
        position: sticky;
        top: 0;
        z-index: 3;
        background: #1f2430;
    }

    .compare-table .sticky-attr {
        position: sticky;
        left: 0;
        z-index: 2;
        background: #111827;
        min-width: 260px;
        max-width: 260px;
        width: 260px;
        font-weight: 600;
    }

    .compare-table .material-col {
        min-width: 180px;
        max-width: 180px;
        width: 180px;
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

def attr_blocks(
    props: dict[str, Any] | None,
    filter_block: str | None = None,
) -> dict[str, Any]:
    parsed = parse_props(props)
    if filter_block:
        val = parsed.get(filter_block)
        return {filter_block: val} if val not in (None, "", {}, []) else {}

    return {
        k: parsed[k]
        for k in ATTR_BLOCKS
        if k in parsed and parsed[k] not in (None, "", {}, [])
    }
def has_attr_block(props, block: str) -> bool:
    return bool(attr_blocks(props, filter_block=block))

def _flatten_obj(
    obj: Any,
    prefix: str = "",
    *,
    combine_value_unit: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    if isinstance(obj, dict):
        if (
            combine_value_unit
            and "value" in obj
            and obj.get("value") not in (None, "")
        ):
            value = str(obj["value"]).strip()
            unit = str(obj.get("unit", "")).strip()
            rows.append({"attribute": prefix, "value": f"{value} {unit}".strip()})
            return rows

        for k, v in obj.items():
            if combine_value_unit and k in {"unit", "flow", "compartment"}:
                continue
            path = f"{prefix}.{k}" if prefix else k
            rows.extend(
                _flatten_obj(v, path, combine_value_unit=combine_value_unit)
            )
        return rows

    if isinstance(obj, list):
        if obj and all(not isinstance(x, (dict, list)) for x in obj):
            rows.append(
                {"attribute": prefix, "value": ", ".join(str(x) for x in obj)}
            )
        else:
            for i, item in enumerate(obj):
                rows.extend(
                    _flatten_obj(
                        item,
                        f"{prefix}[{i}]",
                        combine_value_unit=combine_value_unit,
                    )
                )
        return rows

    if obj not in (None, ""):
        rows.append({"attribute": prefix, "value": str(obj)})

    return rows


def flatten_blocks(
    blocks: dict[str, Any],
    *,
    combine_value_unit: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, val in blocks.items():
        rows.extend(
            _flatten_obj(val, name, combine_value_unit=combine_value_unit)
        )
    if combine_value_unit:
        seen: set[tuple[str, str]] = set()
        deduped = []
        for row in rows:
            key = (row["attribute"], row["value"])
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        return deduped
    return rows

def attrs_to_wide_row(rows: list[dict[str, str]]) -> dict[str, str]:
    return {r["attribute"]: r["value"] for r in rows}





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

@st.cache_data(show_spinner=False)
def fetch_material_node(material_id: str) -> dict[str, Any] | None:
    rows = run_query(
        f"""
        MATCH (n:{NODE_LABEL} {{id: $material_id}})
        RETURN
            n.id AS id,
            n.name AS label,
            properties(n) AS props
        LIMIT 1
        """,
        {"material_id": material_id},
    )
    return rows[0] if rows else None
    
def search_materials(query: str) -> list[dict[str, Any]]:
    q = query.strip().lower()
    if not q:
        return []

    return run_query(
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

        RETURN
            n.id AS id,
            coalesce(n.name, n.id) AS label,
            [x IN nodes(p) | x.id] AS path_ids
        ORDER BY rank_score, label
        """,
        {"q": q},
    )
    
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


def is_flat_dict(obj: Any) -> bool:
    return isinstance(obj, dict) and all(
        not isinstance(v, (dict, list)) for v in obj.values()
    )


def cell_to_display(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def render_nested(key: str | None, obj: Any, level: int = 0) -> None:
    if obj in (None, "", {}, []):
        return

    if isinstance(obj, dict):
        if is_flat_dict(obj):
            if key:
                st.markdown(f"**{key}**")
            st.dataframe(pd.DataFrame([{k: cell_to_display(v) for k, v in obj.items()}]),
                         use_container_width=True, hide_index=True)
            return
        if key:
            st.markdown(f"**{key}**")
        for k, v in obj.items():
            render_nested(k, v, level + 1)
        return

    if isinstance(obj, list):
        if key:
            st.markdown(f"**{key}**")
        if obj and all(isinstance(x, dict) for x in obj):
            st.dataframe(
                pd.DataFrame([{k: cell_to_display(v) for k, v in x.items()} for x in obj]),
                use_container_width=True,
                hide_index=True,
                height=min(38 + 35 * len(obj), 420),
            )
        else:
            st.write(", ".join(str(x) for x in obj))
        return

    if key:
        st.write(f"**{key}:** {obj}")


def render_node_blocks(node: dict[str, Any]) -> None:
    blocks = attr_blocks(
        node.get("props"),
        filter_block=active_filter_block(),
    )
    if not blocks:
        block = active_filter_block()
        st.caption(f"No `{block}` data on this node." if block else "No attribute values on this node.")
        return

    for group_name, group_val in blocks.items():
        st.subheader(group_name)
        render_nested(None, group_val)
        
def node_passes_submaterial_filter(node: dict[str, Any]) -> bool:
    choice = st.session_state.filter_attr_block
    if choice == "(no filter)":
        return True
    return has_attr_block(node.get("props"), choice)

def filter_nodes_by_attr(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in nodes if node_passes_submaterial_filter(n)]


def visible_submaterials(indexes: dict[str, Any], parent_id: str) -> list[dict[str, Any]]:
    direct_children = indexes["children_by_parent"].get(parent_id, [])

    if st.session_state.filter_attr_block == "(no filter)":
        return direct_children

    visible: list[dict[str, Any]] = []

    for child in direct_children:
        if node_passes_submaterial_filter(child):
            visible.append(child)
        else:
            visible.extend(visible_submaterials(indexes, child["id"]))

    visible.sort(key=node_name)
    return visible


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

    current_id = st.session_state.path_ids[-1]
    current_node = indexes["nodes_by_id"].get(current_id)

    # Current node has the filtered block → stay
    if current_node and node_passes_submaterial_filter(current_node):
        return False

    # Current node can show matching submaterials → stay
    if visible_submaterials(indexes, current_id):
        return False

    # Walk up to nearest ancestor that can show matches
    for candidate_id in reversed(st.session_state.path_ids[:-1]):
        if visible_submaterials(indexes, candidate_id):
            new_path = path_to_node(indexes, candidate_id)
            if new_path != st.session_state.path_ids:
                st.session_state.path_ids = new_path
                return True
            return False

    # Last resort: jump to root
    root_id = st.session_state.path_ids[0]
    if st.session_state.path_ids != [root_id]:
        st.session_state.path_ids = [root_id]
        return True

    return False



    
def node_has_values(node: dict[str, Any]) -> bool:
    return bool(flatten_blocks(attr_blocks(node.get("props"))))
    
def active_filter_block() -> str | None:
    choice = st.session_state.get("filter_attr_block", "(no filter)")
    return None if choice == "(no filter)" else choice




def summarize_branch(indexes: dict[str, Any], node_id: str) -> dict[str, Any]:
    direct_children = indexes["children_by_parent"].get(node_id, [])
    descendant_ids = indexes["descendants_by_id"].get(node_id, [])

    populated_direct_children = [
        child for child in direct_children if node_has_values(child)
    ]

    populated_descendants = [
        indexes["nodes_by_id"][desc_id]
        for desc_id in descendant_ids
        if node_has_values(indexes["nodes_by_id"][desc_id])
    ]

    return {
        "direct_children": direct_children,
        "direct_child_count": len(direct_children),
        "descendant_count": len(descendant_ids),
        "populated_direct_children": populated_direct_children,
        "populated_descendant_count": len(populated_descendants),
    }
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




def render_parts_compare(parts: list[dict[str, str]]) -> None:
    if len(parts) < 2:
        st.caption("Select at least 2 materials to compare.")
        return

    rows_by_material: dict[str, dict[str, str]] = defaultdict(dict)
    material_names: dict[str, str] = {}

    for part in parts:
        material_id = part["material_id"]
        material_names[material_id] = part["material_name"]
        rows_by_material[material_id][part["attribute"]] = part["value"]

    ordered_material_ids = list(material_names.keys())
    all_attributes = sorted(
        {
            attribute
            for material_id in ordered_material_ids
            for attribute in rows_by_material[material_id].keys()
        }
    )

    if not all_attributes:
        st.caption("No comparable attributes found.")
        return

    show_only_differences = st.checkbox(
        "Show only differing attributes",
        value=True,
        key="compare_show_only_differences",
    )

    kept_attributes: list[str] = []
    for attribute in all_attributes:
        values = []
        for material_id in ordered_material_ids:
            value = rows_by_material[material_id].get(attribute, "").strip()
            if value:
                values.append(value)
        unique_values = set(values)
        if not show_only_differences or len(unique_values) > 1:
            kept_attributes.append(attribute)

    if not kept_attributes:
        st.caption("No differing attributes across selected materials.")
        return

    compare_rows: list[dict[str, str]] = []
    for material_id in ordered_material_ids:
        row = {
            "material": material_names[material_id],
        }
        for attribute in kept_attributes:
            row[attribute] = rows_by_material[material_id].get(attribute, "")
        compare_rows.append(row)

    df = pd.DataFrame(compare_rows)

    renamed_columns = {
        col: col.replace(".", " › ")
        for col in df.columns
        if col != "material"
    }
    df = df.rename(columns=renamed_columns)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(220 + 48 * len(df), 900),
    )

    st.download_button(
        "Download comparison (CSV)",
        df.to_csv(index=False),
        file_name="material_comparison.csv",
        mime="text/csv",
    )
    #end compare box

def on_nav_child(child_id: str) -> None:
    indexes = st.session_state.get("root_indexes")
    if not indexes or not st.session_state.path_ids:
        return

    current_id = st.session_state.path_ids[-1]
    allowed_ids = {n["id"] for n in visible_submaterials(indexes, current_id)}

    if child_id in allowed_ids:
        st.session_state.path_ids = path_to_node(indexes, child_id)
        st.rerun()

def render_child_branch(indexes, node):
    cname = node_name(node)
    children = indexes["children_by_parent"].get(node["id"], [])

    value_count = len(flatten_blocks(attr_blocks(node.get("props"), active_filter_block())))

    title = cname
    if children:
        title += f" ({len(children)} submaterials)"
    if value_count:
        title += f" [{value_count} values]"

    with st.expander(title, expanded=False):
        cmp_key = f"cmp_child_{node['id']}"
        st.checkbox(
            "Compare",
            value=is_material_in_compare(node["id"]),
            key=cmp_key,
            on_change=on_compare_toggle,
            args=(node["id"], cname, cmp_key),
        )

        bom_key = f"bill_child_{node['id']}"
        st.checkbox(
            "Add to BOM",
            value=is_in_bill(node["id"]),
            key=bom_key,
            on_change=on_bill_toggle,
            args=(node["id"], bom_key),
        )

        render_node_blocks(node)

        for child in children:
            render_child_branch(indexes, child)
            
# =============================================================================
# SECTION 8 — BOM HELPERS
# =============================================================================
def is_in_bill(material_id: str) -> bool:
    for items in st.session_state.bom.values():
        if any(b["id"] == material_id for b in items):
            return True
    return False


def add_to_bill_from_node(node: dict[str, Any], category: str) -> None:
    attr_rows = flatten_blocks(attr_blocks(node.get("props")))
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

    st.caption("Path - navigate center-view submaterials")
    for i, label in enumerate(labels):
        st.button(
            label,
            key=f"crumb_{path_ids[i]}_{i}",
            on_click=on_crumb_click,
            args=(i,),
            use_container_width=True,
        )

def html_escape(value: Any) -> str:
    s = "" if value is None else str(value)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def build_bom_dataframe() -> pd.DataFrame:
    bom_rows: list[dict[str, str]] = []

    for category in sorted(st.session_state.bom.keys()):
        for item in st.session_state.bom[category]:
            node = fetch_material_node(item["id"])
            if not node:
                continue

            row = {
                "category": category,
                "material_id": item["id"],
                "material_name": item["name"],
            }
            #
            for attr_row in flatten_blocks(attr_blocks(node.get("props"))):
                row[attr_row["attribute"]] = attr_row["value"]

            bom_rows.append(row)

    if not bom_rows:
        return pd.DataFrame()

    bom_df = pd.DataFrame(bom_rows)
    fixed_cols = ["category", "material_id", "material_name"]
    attr_cols = sorted(c for c in bom_df.columns if c not in fixed_cols)
    return bom_df[fixed_cols + attr_cols]


def filter_bom_dataframe(
    bom_df: pd.DataFrame,
    selected_categories: list[str],
    selected_materials: list[str],
    selected_attributes: list[str],
    attribute_mode: str,
) -> pd.DataFrame:
    filtered = bom_df.copy()

    if selected_categories:
        filtered = filtered[filtered["category"].isin(selected_categories)]

    if selected_materials:
        filtered = filtered[filtered["material_name"].isin(selected_materials)]

    if selected_attributes:
        existing_selected_attributes = [a for a in selected_attributes if a in filtered.columns]

        if existing_selected_attributes:
            if attribute_mode == "any selected attribute":
                mask = filtered[existing_selected_attributes].notna().any(axis=1)
                filtered = filtered[mask]
            elif attribute_mode == "all selected attributes":
                mask = filtered[existing_selected_attributes].notna().all(axis=1)
                filtered = filtered[mask]

            fixed_cols = ["category", "material_id", "material_name"]
            filtered = filtered[fixed_cols + existing_selected_attributes]

    return filtered

# =============================================================================
# SECTION 10 — SESSION STATE
# =============================================================================
if "has_searched" not in st.session_state:
    st.session_state.has_searched = False
    st.session_state.path_ids = []
    st.session_state.root_indexes = None
    st.session_state.search_feedback = ""
    
if "search_results" not in st.session_state:
    st.session_state.search_results = []
    
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

    # roots dropdown
    roots = get_root_nodes()
    root_map = {r["id"]: r["label"] for r in roots}
    browse_options = [""] + list(root_map.keys())

    current_root_id = (
        st.session_state.path_ids[0]
        if st.session_state.path_ids
        else ""
    )

    browse_pick = st.selectbox(
        "Top level",
        options=browse_options,
        index=browse_options.index(current_root_id)
        if current_root_id in browse_options
        else 0,
        format_func=lambda rid: "— select —" if rid == "" else root_map[rid],
        key="top_level_root_picker",
    )

    if browse_pick != current_root_id:
        if browse_pick:
            st.session_state.has_searched = True
            st.session_state.path_ids = [browse_pick]
            st.session_state.root_indexes = None
            st.session_state.search_feedback = ""
            st.session_state.search_results = []
        else:
            st.session_state.has_searched = False
            st.session_state.path_ids = []
            st.session_state.root_indexes = None
            st.session_state.search_feedback = ""
            st.session_state.search_results = []

        st.rerun()
    # end dropdown roots

    with st.form("global_material_search", clear_on_submit=False):
        search_query = st.text_input("query", placeholder="", label_visibility="collapsed")
        search_submitted = st.form_submit_button("Search")

    if search_submitted:
        #
        q = search_query.strip()
        if not q:
            st.session_state.search_results = []
            st.session_state.search_feedback = "Enter a search term."
        else:
            st.session_state.search_results = search_materials(q)
            if st.session_state.search_results:
                st.session_state.search_feedback = (
                    f"{len(st.session_state.search_results)} match(es) for “{q}”."
                )
            else:
                st.session_state.search_feedback = f"No materials found for “{q}”."

    if st.session_state.search_feedback:
        st.caption(st.session_state.search_feedback)

    if st.session_state.search_results:
        st.markdown("**Search results**")
        for i, hit in enumerate(st.session_state.search_results):
            label = hit.get("label") or hit.get("id") or "Unknown"
            if st.button(label, key=f"search_pick_{i}_{hit['id']}", use_container_width=True):
                path_ids = hit.get("path_ids") or [hit["id"]]
                st.session_state.has_searched = True
                st.session_state.path_ids = path_ids
                st.session_state.root_indexes = None
                st.session_state["top_level_root_picker"] = path_ids[0]
                st.rerun()

    # start filter clear
    filter_pick = st.selectbox(
        "Only show submaterials with:",
        options=FILTER_ATTR_OPTIONS,
        index=FILTER_ATTR_OPTIONS.index(st.session_state.filter_attr_block)
        if st.session_state.filter_attr_block in FILTER_ATTR_OPTIONS
        else 0,
    )

    if filter_pick != st.session_state.filter_attr_block:
        st.session_state.filter_attr_block = filter_pick
        st.session_state.root_indexes = None
        st.rerun()

    if st.session_state.has_searched and st.session_state.path_ids:
        root_id = st.session_state.path_ids[0]
        root_rows = fetch_root_subtree(root_id)
        indexes = build_subtree_indexes(root_rows, root_id)
        st.session_state.root_indexes = indexes
        if apply_filter_auto_dive(indexes):
            st.rerun()
        render_clickable_path(st.session_state.path_ids, indexes)
    # end filter clear

    # compare list
    if st.session_state.compare_materials:
        st.divider()
        st.markdown("**Compare List**")
        compare_groups = defaultdict(list)
        for m in st.session_state.compare_materials:
            compare_groups[m.get("category", "Uncategorized")].append(m)
        for cat in sorted(compare_groups.keys()):
            st.markdown(f"**{cat}**")
            for m in compare_groups[cat]:
                name_col, reject_col = st.columns([6, 1])
                with name_col:
                    st.caption(f"• {m['name']}")
                with reject_col:
                    if st.button("✕", key=f"reject_compare_{m['id']}"):
                        remove_material_from_compare(m["id"])
                        st.rerun()
        if st.button("Clear compare list", use_container_width=True):
            st.session_state.compare_materials = []
            st.session_state.compare_parts = []
            st.session_state.show_compare_view = False
            st.rerun()
    # end compare list

    st.divider()
    st.caption("Bill of materials")
    if not st.session_state.bom:
        st.caption("Empty.")
    else:
        for cat in sorted(st.session_state.bom.keys()):
            st.markdown(f"**{cat}**")
            for item in st.session_state.bom[cat]:
                name_col, reject_col = st.columns([6, 1])
                with name_col:
                    st.caption(f"• {item['name']}")
                with reject_col:
                    if st.button("✕", key=f"reject_bom_{cat}_{item['id']}"):
                        remove_from_bill(item["id"])
                        st.rerun()

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
tab_path, tab_table, tab_compare, tab_bom = st.tabs(
    ["Path + explore", "All values (table)", "Compare", "Export BOM"])

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

    if st.session_state.compare_materials:
        st.caption("view compared materials.")

    for i, pn in enumerate(path_nodes):
        is_current = i == len(path_nodes) - 1
        name = node_name(pn)
       
        
        #
        value_count = len(flatten_blocks(attr_blocks(pn.get("props"), active_filter_block())))
        all_children = indexes["children_by_parent"].get(pn["id"], [])

        title = name

        if all_children:
            title += f" ({len(all_children)} submaterials)"

        if value_count:
            title += f" [{value_count} values]"

        with st.expander(title, expanded=is_current):

            render_node_blocks(pn)

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

    current = path_nodes[-1]
    children = visible_submaterials(indexes, current["id"])

    block = active_filter_block()
    if block:
        st.markdown(f"**Submaterials** — showing nodes with `{block}`")
    else:
        st.markdown("**Submaterials**")

    if not children:
        if st.session_state.filter_attr_block == "(no filter)":
            st.caption("No submaterials here.")
        else:
            st.caption(f"No submaterials with `{st.session_state.filter_attr_block}` under this node.")
    else:
        for child in children:
            render_child_branch(indexes, child)
# --- TAB 2 ---
with tab_table:
    st.subheader("All materials under this node — every extracted value")
    all_rows: list[dict[str, str]] = []

    for row in subtree:
        p = parse_props(row["props"])
        mat_name = p.get("name") or row["label"]
        depth = row["depth"]
        for attr_row in flatten_blocks(attr_blocks(row["props"])):
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
            mime="text/csv")

# --- TAB 3 ---
with tab_compare:
    st.subheader("Compare materials")

    if len(st.session_state.compare_materials) < 2:
        branch = summarize_branch(indexes, current_id)

        if branch["direct_child_count"] == 0:
            st.info("Select at least 2 materials with the Compare checkbox.")
        else:
            st.info(
                f"{node_name(node)} is a category node with "
                f"{branch['direct_child_count']} direct submaterials and "
                f"{branch['populated_descendant_count']} populated descendants."
            )

            if branch["populated_direct_children"]:
                if st.button("Compare direct submaterials"):
                    st.session_state.compare_materials = [
                        {"id": child["id"], "name": node_name(child)}
                        for child in branch["populated_direct_children"]
                    ]
                    st.rerun()
            else:
                st.caption("No direct submaterials under this node have comparable values.")
    else:
        compare_parts: list[dict[str, str]] = []

        for material in st.session_state.compare_materials:
            material_node = fetch_material_node(material["id"])
            if not material_node:
                continue

            attr_rows = flatten_blocks(
                attr_blocks(material_node.get("props")),
                combine_value_unit=True,
            )
            for attr_row in attr_rows:
                compare_parts.append(
                    {
                        "key": part_compare_key(material["id"], attr_row["attribute"]),
                        "material_id": material["id"],
                        "material_name": material["name"],
                        "attribute": attr_row["attribute"],
                        "value": attr_row["value"],
                    }
                )

        if not compare_parts:
            st.info("No comparable attributes found for the selected materials.")
        else:
            render_parts_compare(compare_parts)
            
# --- TAB 4 ---
with tab_bom:
    st.subheader("Export BOM")

    bom_df = build_bom_dataframe()

    if bom_df.empty:
        st.info("No materials in the bill of materials yet.")
    else:
        fixed_cols = ["category", "material_id", "material_name"]
        attr_cols = [c for c in bom_df.columns if c not in fixed_cols]

        selected_categories = st.multiselect(
            "Filter categories",
            options=sorted(bom_df["category"].dropna().unique().tolist()),
        )

        selected_materials = st.multiselect(
            "Filter materials",
            options=sorted(bom_df["material_name"].dropna().unique().tolist()),
        )

        selected_attributes = st.multiselect(
            "Keep only these attributes in table/export",
            options=sorted(attr_cols),
        )

        attribute_mode = st.radio(
            "Attribute row filter",
            options=["no row filter", "any selected attribute", "all selected attributes"],
            horizontal=True,
        )

        filtered_bom_df = filter_bom_dataframe(
            bom_df=bom_df,
            selected_categories=selected_categories,
            selected_materials=selected_materials,
            selected_attributes=selected_attributes,
            attribute_mode=attribute_mode,
        )

        st.dataframe(
            filtered_bom_df,
            use_container_width=True,
            hide_index=True,
            height=700,
        )

        bom_export_name = st.text_input(
            "Export file name",
            value="bill_of_materials_filtered",
            key="bom_export_name",
            help="Enter the CSV file name without .csv",
        ).strip()

        if not bom_export_name:
            bom_export_name = "bill_of_materials_filtered"

        if not bom_export_name.lower().endswith(".csv"):
            bom_export_name = f"{bom_export_name}.csv"

        st.download_button(
            "Export filtered BOM to CSV",
            filtered_bom_df.to_csv(index=False),
            file_name=bom_export_name,
            mime="text/csv",
        )
