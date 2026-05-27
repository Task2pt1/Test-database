# app.py
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pandas as pd
import streamlit as st
from neo4j import GraphDatabase, Driver

st.set_page_config(page_title="Material Ontology Explorer", layout="wide")

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
ATTRIBUTE_TEMPLATE_KEYS = (
    "name", "id", "code", "database", "placement", "vector",
    "synonyms", "comment", "citation", "standards", "region",
    "engineering", "activity", "lcia", "material_cost", "notes",
)
NODE_LABEL = "Material"
CHILD_REL = "HAS_CHILD"

# -----------------------------------------------------------------------------
# Neo4j
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Cypher — all navigation on YOUR n.id (hash), not elementId
# -----------------------------------------------------------------------------
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
        RETURN n.id AS id,
               n.name AS label,
               properties(n) AS props,
               EXISTS {{ (n)-[:{CHILD_REL}]->(:{NODE_LABEL}) }} AS has_children
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

def get_subtree(root_id: str) -> list[dict[str, Any]]:
    return run_query(f"""
        MATCH p = (root:{NODE_LABEL})-[:{CHILD_REL}*0..]->(n:{NODE_LABEL})
        WHERE root.id = $root_id
        WITH n, min(length(p)) AS depth, head(collect(p)) AS chosen_path
        RETURN
          n.id AS node_id,
          n.name AS node_label,
          properties(n) AS node_props,
          depth,
          [x IN nodes(chosen_path) | x.name] AS path_labels
        ORDER BY depth, node_label
    """, {"root_id": root_id})

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def safe_join_path(parts: list[str]) -> str:
    return " → ".join(p for p in parts if p)

def available_attributes(props: dict[str, Any]) -> list[str]:
    return [
        k for k in ATTRIBUTE_TEMPLATE_KEYS
        if props.get(k) not in (None, "", [], {})
    ]

def build_node_rows(subtree_rows: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for row in subtree_rows:
        props = row["node_props"] or {}
        base = {
            "selected": True,
            "id": row["node_id"],
            "name": row["node_label"],
            "depth": row["depth"],
            "path": safe_join_path(row["path_labels"] or []),
        }
        for k in available_attributes(props):
            base[k] = props[k]
        rows.append(base)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["depth", "path", "name"], kind="stable").reset_index(drop=True)
    return df

def to_excel(selected: pd.DataFrame, full: pd.DataFrame) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        selected.to_excel(w, sheet_name="selected", index=False)
        full.to_excel(w, sheet_name="full_subtree", index=False)
    buf.seek(0)
    return buf.read()

# -----------------------------------------------------------------------------
# Session
# -----------------------------------------------------------------------------
if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.title("Material Ontology Explorer")

with st.sidebar:
    st.header("Navigation")
    roots = get_root_nodes()
    if not roots:
        st.error("No Material nodes found.")
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
        st.caption(safe_join_path(get_breadcrumb_labels(st.session_state.path_ids)))

if not st.session_state.path_ids:
    st.info("Select a root in the sidebar.")
    st.stop()

current_id = st.session_state.path_ids[-1]
node = get_node_summary(current_id)
if not node:
    st.error("Node not found.")
    st.stop()

props = node["props"] or {}
col1, col2 = st.columns([1.2, 2.0])

with col1:
    st.subheader("Current material")
    st.write(f"**Name:** {props.get('name', node['label'])}")
    st.write(f"**id:** `{props.get('id', '')}`")
    st.write(f"**code:** `{props.get('code', '')}`")
    st.write(f"**Has children:** {'Yes' if node['has_children'] else 'No'}")

    avail = available_attributes(props)
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
    st.subheader("Subtree")
    scope = st.radio("Show from", ["Current node", "Top-level root"], horizontal=True)
    root_id = current_id if scope == "Current node" else st.session_state.path_ids[0]

    df = build_node_rows(get_subtree(root_id))
    if df.empty:
        st.warning("No subtree rows.")
        st.stop()

    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    edited = st.data_editor(
        df,
        column_config={"selected": st.column_config.CheckboxColumn("Export")},
        disabled=[c for c in df.columns if c != "selected"],
        hide_index=True,
        use_container_width=True,
    )
    st.download_button(
        "Download Excel",
        data=to_excel(edited[edited["selected"]], df),
        file_name="materials_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
