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

def get_node_values(node_id: str):
    props = run_query("""
        MATCH (n)
        WHERE elementId(n) = $id
        RETURN properties(n) AS props
    """, {"id": node_id})
    props = props[0]["props"] if props else {}

    details = run_query("""
        MATCH (n)-[r]->(m)
        WHERE elementId(n) = $id
          AND type(r) <> 'HAS_CHILD'
        RETURN
            type(r) AS rel,
            m.name AS name,
            m.value AS value,
            properties(m) AS props
    """, {"id": node_id})

    clean_details = []
    for d in details:
        label = d.get("name") or d.get("value") or d["rel"]
        clean_details.append({
            "label": label,
            "data": d.get("props", {})
        })
    return props, clean_details

# ----------------------------
# DRILL-DOWN PATH
# ----------------------------

st.subheader("Browse Hierarchy")

if "path" not in st.session_state:
    st.session_state.path = []

col1, _ = st.columns([1, 4])
with col1:
    if st.button("Start over"):
        st.session_state.path = []
        st.rerun()

if st.session_state.path:
    st.caption(" → ".join(p["label"] for p in st.session_state.path))

if not st.session_state.path:
    options = get_root_nodes()
    box_label = "Choose a main category"
else:
    parent_id = st.session_state.path[-1]["id"]
    options = get_children(parent_id)
    box_label = f"Choose under {st.session_state.path[-1]['label']}"

if not options:
    st.info("No more levels below this node.")
else:
    ids = [o["id"] for o in options]
    labels = {o["id"]: o["label"] for o in options}
    picked = st.selectbox(
        box_label,
        options=[None] + ids,
        format_func=lambda x: "— select —" if x is None else labels[x],
        key=f"level_{len(st.session_state.path)}",
    )
    if picked is not None:
        st.session_state.path.append({"id": picked, "label": labels[picked]})
        st.rerun()

if st.session_state.path:
    current = st.session_state.path[-1]
    props, details = get_node_values(current["id"])
    st.markdown(f"### {current['label']}")
    st.json(props)
    if details:
        st.dataframe(details)
