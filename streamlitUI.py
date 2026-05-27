# app.py — Material Ontology Explorer (view + bill of materials, no export)

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from neo4j import GraphDatabase, Driver

st.set_page_config(page_title="Material Ontology Explorer", layout="wide")

ATTRIBUTE_TEMPLATE_KEYS = (
    "name", "id", "code", "database", "placement", "vector",
    "synonyms", "comment", "citation", "standards", "region",
    "engineering", "activity", "lcia", "material_cost", "notes",
)
NODE_LABEL = "Material"
CHILD_REL = "HAS_CHILD"

UI_ATTRS = [
    k for k in ATTRIBUTE_TEMPLATE_KEYS
    if k not in ("name", "id", "code", "database", "vector", "placement")
]

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

def get_root_nodes() -> list[dict[str, str]]:
    return run_query(f"""
        MATCH (n:{NODE_LABEL})
        WHERE NOT ()-[:{CHILD_REL}]->(n)
        RETURN n.id AS id, n.name AS label
        ORDER BY label
    """)

def get_node_summary(material_id: str) -> dict[str, Any] | None:
    rows = run_query(f"""
        MATCH (n:{NODE_LABEL} {{id: $id}})
        RETURN n.id AS id, n.name AS label, properties(n) AS props
    """, {"id": material_id})
    return rows[0] if rows else None

def get_children(material_id: str) -> list[dict[str, str]]:
    return run_query(f"""
        MATCH (:{NODE_LABEL} {{id: $id}})-[:{CHILD_REL}]->(c:{NODE_LABEL})
        RETURN c.id AS id, c.name AS label
        ORDER BY label
    """, {"id": material_id})

def get_breadcrumb_labels(path_ids: list[str]) -> list[str]:
    if not path_ids:
        return []
    rows = run_query("""
        UNWIND $ids AS id
        MATCH (n:Material {id: id})
        RETURN id, n.name AS label
    """, {"ids": path_ids})
    m = {r["id"]: r["label"] for r in rows}
    return [m.get(i, i) for i in path_ids]

def get_descendants(root_id: str) -> list[dict[str, Any]]:
    return run_query(f"""
        MATCH p = (root:{NODE_LABEL})-[:{CHILD_REL}*1..]->(n:{NODE_LABEL})
        WHERE root.id = $root_id
        RETURN n.id AS id, n.name AS label, properties(n) AS props
        ORDER BY label
    """, {"root_id": root_id})

def attrs_for_props(props: dict[str, Any]) -> dict[str, Any]:
    return {
        k: props[k]
        for k in UI_ATTRS
        if props.get(k) not in (None, "", [], {})
    }

if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

if "bom" not in st.session_state:
    st.session_state.bom = []

st.title("Material Ontology Explorer")

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
            cur = st.session_state.path_ids[level] if level < len(st.session_state.path_ids) else None
            pick = st.selectbox(
                f"Level {level + 1}",
                [None] + list(kid_map.keys()),
                index=([None] + list(kid_map.keys())).index(cur) if cur in kid_map else 0,
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
    st.subheader("Bill of materials")
    if not st.session_state.bom:
        st.caption("Empty.")
    else:
        for i, item in enumerate(st.session_state.bom, 1):
            st.write(f"{i}. {item['name']}")
        if st.button("Clear bill", use_container_width=True):
            st.session_state.bom = []
            st.rerun()

if not st.session_state.path_ids:
    st.info("Select a root in the sidebar.")
    st.stop()

current_id = st.session_state.path_ids[-1]
node = get_node_summary(current_id)
if not node:
    st.error("Could not load this material.")
    st.stop()

props = node["props"] or {}

col1, col2 = st.columns([1.2, 2.0])

with col1:
    st.subheader("Current material")
    st.write(f"**{props.get('name', node['label'])}**")

    avail = [k for k in UI_ATTRS if props.get(k) not in (None, "", [], {})]
    st.subheader("Attributes")
    if avail:
        st.dataframe(
            pd.DataFrame([{"attribute": k, "value": props[k]} for k in avail]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No attributes on this node.")

with col2:
    st.subheader("Pick materials")
    scope = st.radio("List", ["Children here", "All under top root"], horizontal=True)

    if scope == "Children here":
        items = get_children(current_id)
        table_rows = [{"bill": False, "name": x["label"], "_id": x["id"]} for x in items]
    else:
        items = get_descendants(st.session_state.path_ids[0])
        table_rows = [
            {"bill": False, "name": x["label"], "_id": x["id"], **attrs_for_props(x["props"] or {})}
            for x in items
        ]

    if not table_rows:
        st.caption("Nothing to pick at this level.")
    else:
        df = pd.DataFrame(table_rows)
        edited = st.data_editor(
            df.drop(columns=["_id"]),
            column_config={"bill": st.column_config.CheckboxColumn("Bill")},
            disabled=[c for c in df.columns if c != "bill" and c != "_id"],
            hide_index=True,
            use_container_width=True,
            key="pick_editor",
        )

        if st.button("Add checked to bill"):
            for i, row in edited.iterrows():
                if not row.get("bill"):
                    continue
                mid = df.loc[i, "_id"]
                name = row["name"]
                if not any(b["id"] == mid for b in st.session_state.bom):
                    st.session_state.bom.append({"id": mid, "name": name})
            st.rerun()
