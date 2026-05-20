import streamlit as st
from neo4j import GraphDatabase
from typing import Any

uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]

driver = GraphDatabase.driver(uri, auth=(user, password))

st.set_page_config(page_title="AIF Graph Viewer 2", layout="wide")
st.title("AIF Graph Viewer 2")

def run_query(query: str, params: dict[str, Any] | None = None):
    with driver.session() as session:
        return [r.data() for r in session.run(query, params or {})]

def get_root_nodes():
    rows = run_query("""
        MATCH (n:String)
        WHERE NOT ()-[:HAS_CHILD]->(n)
        RETURN elementId(n) AS id, n.value AS label
        ORDER BY label
    """)
    seen = set()
    out = []
    for r in rows:
        if r["label"] not in seen:
            seen.add(r["label"])
            out.append(r)
    return out

def get_children(node_id: str):
    return run_query("""
        MATCH (n:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(n) = $id
        RETURN elementId(c) AS id, c.value AS label
        ORDER BY label
    """, {"id": node_id})


# ----------------------------
# DRILL-DOWN PATH
# ----------------------------
def get_node_data(node_id: str):
    return run_query("""
        MATCH (n)
        WHERE elementId(n) = $id
        OPTIONAL MATCH (n)-[r]-(m)
        RETURN
            labels(n) AS labels,
            properties(n) AS properties,
            type(r) AS relationship,
            labels(m) AS neighbor_labels,
            properties(m) AS neighbor_properties,
            startNode(r) = n AS outgoing
        ORDER BY relationship
    """, {"id": node_id})

# ----------------------------
# INTERFACE
# ----------------------------
st.subheader("Browse materials")

if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

level = 0
while level <= len(st.session_state.path_ids):
    options = get_root_nodes() if level == 0 else get_children(st.session_state.path_ids[level - 1])
    if not options:
        break

    ids = [o["id"] for o in options]
    labels = {o["id"]: o["label"] for o in options}

    if level < len(st.session_state.path_ids):
        current = st.session_state.path_ids[level]
        picked = st.selectbox(
            f"Level {level + 1}",
            options=ids,
            index=ids.index(current),
            key=f"level_{level}",
            format_func=lambda x: labels[x],
        )
        if picked != current:
            st.session_state.path_ids = st.session_state.path_ids[:level] + [picked]
            st.rerun()
    else:
        picked = st.selectbox(
            f"Level {level + 1}",
            options=[None] + ids,
            index=0,
            key=f"level_{level}",
            format_func=lambda x: "— select —" if x is None else labels[x],
        )
        if picked is None:
            break
        st.session_state.path_ids.append(picked)
        st.rerun()

    level += 1

if st.session_state.path_ids:
    node_id = st.session_state.path_ids[-1]
    st.caption(" → ".join(fetch_record(nid)["name"] for nid in st.session_state.path_ids))
    show_record(fetch_record(node_id), is_category=has_children(node_id))
