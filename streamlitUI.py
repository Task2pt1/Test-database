# app.py — Material Ontology Explorer (view only, no export)

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from neo4j import GraphDatabase, Driver

# =============================================================================
# PAGE
# =============================================================================
st.set_page_config(page_title="Material Ontology Explorer", layout="wide")

# =============================================================================
# CONFIG (lines ~15–25)
# =============================================================================
ATTRIBUTE_TEMPLATE_KEYS = (
    "name", "id", "code", "database", "placement", "vector",
    "synonyms", "comment", "citation", "standards", "region",
    "engineering", "activity", "lcia", "material_cost", "notes",
)
NODE_LABEL = "Material"
CHILD_REL = "HAS_CHILD"

# Attributes hidden from UI tables (identity / technical)
UI_ATTRS = [
    k for k in ATTRIBUTE_TEMPLATE_KEYS
    if k not in ("name", "id", "code", "database", "vector", "placement")
]

# =============================================================================
# NEO4J CONNECTION (lines ~35–45)
# =============================================================================
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

# =============================================================================
# CYPHER QUERIES (lines ~50–95)
# =============================================================================
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

def get_subtree(root_id: str) -> list[dict[str, Any]]:
    return run_query(f"""
        MATCH p = (root:{NODE_LABEL})-[:{CHILD_REL}*0..]->(n:{NODE_LABEL})
        WHERE root.id = $root_id
        WITH n, min(length(p)) AS depth, head(collect(p)) AS chosen_path
        RETURN
          n.name AS node_label,
          properties(n) AS node_props,
          depth,
          [x IN nodes(chosen_path) | x.name] AS path_labels
        ORDER BY depth, node_label
    """, {"root_id": root_id})

# =============================================================================
# HELPERS (lines ~100–130)
# =============================================================================
def safe_join_path(parts: list[str]) -> str:
    return " → ".join(p for p in parts if p)

def build_subtree_table(subtree_rows: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for row in subtree_rows:
        props = row["node_props"] or {}
        rec = {
            "name": row["node_label"],
            "depth": row["depth"],
            "path": safe_join_path(row["path_labels"] or []),
        }
        for k in UI_ATTRS:
            if props.get(k) not in (None, "", [], {}):
                rec[k] = props[k]
        rows.append(rec)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["depth", "path", "name"], kind="stable").reset_index(drop=True)
    return df

# =============================================================================
# SESSION STATE (lines ~135–140)
# =============================================================================
if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

# =============================================================================
# TITLE (line ~145)
# =============================================================================
st.title("Material Ontology Explorer")

# =============================================================================
# SIDEBAR — navigation (lines ~150–210)
# =============================================================================
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
        st.caption(safe_join_path(get_breadcrumb_labels(st.session_state.path_ids)))

# =============================================================================
# STOP if nothing selected (lines ~215–220)
# =============================================================================
if not st.session_state.path_ids:
    st.info("Select a root in the sidebar.")
    st.stop()

# =============================================================================
# LOAD CURRENT NODE (lines ~225–235)
# =============================================================================
current_id = st.session_state.path_ids[-1]
node = get_node_summary(current_id)
if not node:
    st.error("Could not load this material.")
    st.stop()

props = node["props"] or {}

# =============================================================================
# MAIN — two columns (lines ~240–280)
# =============================================================================
col1, col2 = st.columns([1.2, 2.0])

# --- LEFT: current material + attributes ---
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

# --- RIGHT: subtree table ---
with col2:
    st.subheader("Subtree")
    scope = st.radio("Show from", ["Current node", "Top-level root"], horizontal=True)
    root_id = current_id if scope == "Current node" else st.session_state.path_ids[0]

    df = build_subtree_table(get_subtree(root_id))
    if df.empty:
        st.warning("No rows in this subtree.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)

# =============================================================================
# END OF FILE — no export, no download, nothing below here
# =============================================================================
