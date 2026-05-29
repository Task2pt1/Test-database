# streamlitUI.py — Material Ontology Explorer (full extraction)

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st
from neo4j import Driver, GraphDatabase

st.set_page_config(page_title="Material Ontology Explorer", layout="wide")

NODE_LABEL = "Material"
CHILD_REL = "HAS_CHILD"

# attribute blocks stored on Material nodes (JSON strings after upload)
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


@st.cache_resource
def get_driver() -> Driver:
    return GraphDatabase.driver(
        st.secrets["NEO4J_URI"],
        auth=(st.secrets["NEO4J_USERNAME"], st.secrets["NEO4J_PASSWORD"]),
    )


driver = get_driver()


def run_query(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with driver.session() as session:
        return [r.data() for r in session.run(query, params or {})]


# -------------------------------------------------
# Parse Neo4j properties → usable Python
# -------------------------------------------------
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
    """Every scalar measurement/value at any depth."""
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
    """All measurable / literal values on one Material node."""
    parsed = parse_props(props)
    rows: list[dict[str, str]] = []

    for key in ATTR_BLOCKS:
        if key not in parsed or parsed[key] in (None, "", {}, []):
            continue
        rows.extend(flatten_leaves(parsed[key], key))

    # anything else stored on the node
    for k, v in parsed.items():
        if k in META_KEYS or k in ATTR_BLOCKS:
            continue
        rows.extend(flatten_leaves(v, k))

    return rows


# -------------------------------------------------
# Neo4j queries — ALWAYS fetch properties(n)
# -------------------------------------------------
def get_root_nodes() -> list[dict[str, str]]:
    return run_query(
        f"""
        MATCH (n:{NODE_LABEL})
        WHERE NOT ()-[:{CHILD_REL}]->(n)
        RETURN n.id AS id, n.name AS label
        ORDER BY label
        """
    )


def get_node(material_id: str) -> dict[str, Any] | None:
    rows = run_query(
        f"""
        MATCH (n:{NODE_LABEL} {{id: $id}})
        RETURN n.id AS id, n.name AS label, properties(n) AS props
        """,
        {"id": material_id},
    )
    return rows[0] if rows else None


def get_children(material_id: str) -> list[dict[str, Any]]:
    """Direct submaterials WITH full properties."""
    return run_query(
        f"""
        MATCH (:{NODE_LABEL} {{id: $id}})-[:{CHILD_REL}]->(c:{NODE_LABEL})
        RETURN c.id AS id, c.name AS label, properties(c) AS props
        ORDER BY label
        """,
        {"id": material_id},
    )


def get_subtree(material_id: str) -> list[dict[str, Any]]:
    """This node + every descendant, with depth."""
    return run_query(
        f"""
        MATCH (root:{NODE_LABEL} {{id: $root_id}})
        OPTIONAL MATCH p = (root)-[:{CHILD_REL}*0..]->(n:{NODE_LABEL})
        WITH n, min(length(p)) AS depth
        RETURN n.id AS id, n.name AS label, properties(n) AS props, depth
        ORDER BY depth, label
        """,
        {"root_id": material_id},
    )


def get_breadcrumb_labels(path_ids: list[str]) -> list[str]:
    if not path_ids:
        return []
    rows = run_query(
        """
        UNWIND $ids AS id
        MATCH (n:Material {id: id})
        RETURN id, n.name AS label
        """,
        {"ids": path_ids},
    )
    label_map = {r["id"]: r["label"] for r in rows}
    return [label_map.get(i, i) for i in path_ids]


def get_ontology_category(material_id: str) -> str:
    rows = run_query(
        """
        MATCH (m:Material {id: $id})
        OPTIONAL MATCH (root:Material)-[:HAS_CHILD*]->(m)
        WHERE NOT ()-[:HAS_CHILD]->(root)
        WITH m, head(collect(root)) AS r
        RETURN coalesce(r.name, m.name) AS category
        """,
        {"id": material_id},
    )
    return rows[0]["category"] if rows else "Unknown"

# -------------------------------------------------
# Wide layout + bill of materials helpers
# -------------------------------------------------
def attrs_to_wide_row(attr_rows: list[dict[str, str]]) -> dict[str, str]:
    return {r["attribute"]: r["value"] for r in attr_rows}


def render_attributes_wide(
    attr_rows: list[dict[str, str]],
    *,
    material_name: str | None = None,
) -> None:
    if not attr_rows:
        st.caption("No attribute values on this node.")
        return

    row = attrs_to_wide_row(attr_rows)
    if material_name:
        row = {"material": material_name, **row}

    st.dataframe(
        pd.DataFrame([row]),
        use_container_width=True,
        hide_index=True,
    )


def subtree_to_wide_df(subtree: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for node in subtree:
        props = parse_props(node.get("props"))
        name = props.get("name") or node.get("label")
        attr_rows = extract_attribute_rows(node.get("props") or {})

        row: dict[str, Any] = {
            "depth": node["depth"],
            "material": name,
            "_id": node["id"],
        }
        row.update(attrs_to_wide_row(attr_rows))
        rows.append(row)

    return pd.DataFrame(rows)


def add_to_bill(material_id: str, name: str, attr_rows: list[dict[str, str]]) -> None:
    category = get_ontology_category(material_id)
    entry = {
        "id": material_id,
        "name": name,
        "values": attrs_to_wide_row(attr_rows),
    }

    st.session_state.bom.setdefault(category, [])
    if not any(b["id"] == material_id for b in st.session_state.bom[category]):
        st.session_state.bom[category].append(entry)


def render_bill_sidebar() -> None:
    st.subheader("Bill of materials")
    if not st.session_state.bom:
        st.caption("Empty.")
        return

    for cat in sorted(st.session_state.bom.keys()):
        st.markdown(f"**{cat}**")
        for i, item in enumerate(st.session_state.bom[cat], 1):
            st.write(f"{i}. {item['name']}")
            vals = item.get("values") or {}
            if vals:
                preview = "; ".join(f"{k}={v}" for k, v in list(vals.items())[:3])
                if len(vals) > 3:
                    preview += f" … (+{len(vals) - 3} more)"
                st.caption(preview)

    if st.button("Clear bill", use_container_width=True):
        st.session_state.bom = {}
        st.rerun()

# -------------------------------------------------
# UI: render one material + recurse into submaterials
# -------------------------------------------------
def render_material_node(
    node: dict[str, Any],
    *,
    depth: int = 0,
    expanded: bool = False,
) -> None:
    props = parse_props(node.get("props"))
    name = props.get("name") or node.get("label") or node["id"]
    attr_rows = extract_attribute_rows(node.get("props") or {})
    children = get_children(node["id"])

    indent = "　" * depth
    title = f"{indent}{name}"
    if children:
        title += f"  ({len(children)} submaterials)"
    if attr_rows:
        title += f"  [{len(attr_rows)} values]"

    with st.expander(title, expanded=expanded):
        st.caption(
            f"id: `{props.get('id', node['id'])}` · "
            f"code: `{props.get('code', '')}` · "
            f"database: `{props.get('database', '')}`"
        )

        btn_col, _ = st.columns([1, 3])
        with btn_col:
            if st.button("Add to bill", key=f"bill_{node['id']}_{depth}"):
                add_to_bill(node["id"], name, attr_rows)
                st.rerun()

        if attr_rows:
            st.markdown("**Attribute values**")
            render_attributes_wide(attr_rows, material_name=name)
        else:
            st.caption("No attribute values on this node.")

        for key in ATTR_BLOCKS:
            if key in props and props[key] not in (None, "", {}, []):
                with st.expander(f"{key} (structured)"):
                    st.json(props[key])

        if children:
            st.markdown("**Submaterials**")
            for child in children:
                render_material_node(child, depth=depth + 1, expanded=False)

# -------------------------------------------------
# Session state
# -------------------------------------------------
if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

if "bom" not in st.session_state:
    st.session_state.bom = {}


st.title("Material Ontology Explorer")

# -------------------------------------------------
# Sidebar navigation (unchanged logic)
# -------------------------------------------------
with st.sidebar:
    st.header("Navigation")

    roots = get_root_nodes()
    if not roots:
        st.error("No Material nodes in database.")
        st.stop()

    root_map = {r["id"]: r["label"] for r in roots}
    root_pick = st.selectbox(
        "Root",
        [None] + list(root_map.keys()),
        format_func=lambda x: "— select —" if x is None else root_map[x],
    )

    if root_pick is None:
        st.session_state.path_ids = []
    else:
        if not st.session_state.path_ids or st.session_state.path_ids[0] != root_pick:
            st.session_state.path_ids = [root_pick]

        level = 1
        while True:
            kids = get_children(st.session_state.path_ids[level - 1])
            if not kids:
                break
            kid_map = {c["id"]: c["label"] for c in kids}
            cur = (
                st.session_state.path_ids[level]
                if level < len(st.session_state.path_ids)
                else None
            )
            options = [None] + list(kid_map.keys())
            pick = st.selectbox(
                f"Level {level + 1}",
                options,
                index=options.index(cur) if cur in kid_map else 0,
                format_func=lambda x: "— stop —" if x is None else kid_map[x],
                key=f"lvl_{level}",
            )
            if pick is None:
                st.session_state.path_ids = st.session_state.path_ids[:level]
                break
            if level < len(st.session_state.path_ids):
                st.session_state.path_ids = st.session_state.path_ids[:level] + [pick]
            else:
                st.session_state.path_ids.append(pick)
            level += 1

    if st.session_state.path_ids:
        st.caption(" → ".join(get_breadcrumb_labels(st.session_state.path_ids)))

    st.divider()
    render_bill_sidebar()


if not st.session_state.path_ids:
    st.info("Select a root in the sidebar.")
    st.stop()


current_id = st.session_state.path_ids[-1]
node = get_node(current_id)
if not node:
    st.error("Could not load this material.")
    st.stop()

props = parse_props(node["props"])
name = props.get("name") or node["label"]
direct_children = get_children(current_id)
subtree = get_subtree(current_id)

st.header(name)
st.caption(
    f"Direct submaterials: **{len(direct_children)}** · "
    f"Total nodes in subtree: **{len(subtree)}**"
)

# -------------------------------------------------
# MAIN EXTRACTION VIEWS
# -------------------------------------------------
tab_tree, tab_table, tab_bom = st.tabs(
    ["Submaterial tree + values", "Flat extraction table", "Pick for BOM"]
)

with tab_tree:
    st.subheader("Submaterials and attribute values")
    st.caption(
        "Each row is a Material node from Neo4j. "
        "Submaterials = HAS_CHILD edges. "
        "Values = properties on that node (engineering, activity, notes, …)."
    )
    render_material_node(node, depth=0, expanded=True)

with tab_table:
    st.subheader("All materials under this node — wide attribute table")
    st.caption(
        "Each row = one material. "
        "Each column = one attribute path (engineering.…, activity.…, notes, …)."
    )

    wide_df = subtree_to_wide_df(subtree)

    if wide_df.empty:
        st.info("No materials in this subtree.")
    else:
        show_df = wide_df.drop(columns=["_id"], errors="ignore")
        st.dataframe(show_df, use_container_width=True, hide_index=True)

        st.download_button(
            "Download extracted values (CSV)",
            show_df.to_csv(index=False),
            file_name=f"{name}_extract.csv",
            mime="text/csv",
        )

with tab_bom:
    st.subheader("Pick materials for bill")
    scope = st.radio(
        "List",
        ["Children here", "All under this node"],
        horizontal=True,
    )

    items = direct_children if scope == "Children here" else [r for r in subtree if r["depth"] > 0]

    table_rows = []
    for x in items:
        p = parse_props(x["props"])
        mat_name = p.get("name") or x["label"]
        attr_rows = extract_attribute_rows(x["props"] or {})
        wide = attrs_to_wide_row(attr_rows)

        row = {"bill": False, "material": mat_name, "_id": x["id"]}
        row.update(wide)
        table_rows.append(row)

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
                mat_name = row["material"]
                attr_rows = [
                    {"attribute": c, "value": str(row[c])}
                    for c in df.columns
                    if c not in ("bill", "material", "_id")
                    and pd.notna(row[c])
                    and row[c] != ""
                ]
                add_to_bill(mid, mat_name, attr_rows)
            st.rerun()
