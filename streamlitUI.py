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
def has_children(node_id: str) -> bool:
    r = run_query("""
        MATCH (n:String)-[:HAS_CHILD]->(:String)
        WHERE elementId(n) = $id
        RETURN count(*) > 0 AS yes
    """, {"id": node_id})
    return bool(r and r[0]["yes"])

def fetch_record(node_id: str) -> dict:
    """One row from the DB for the selected node."""
    r = run_query("""
        MATCH (n:String)
        WHERE elementId(n) = $id
        RETURN n.value AS name, properties(n) AS props
    """, {"id": node_id})
    if not r:
        return {}
    name = r[0]["name"]
    props = r[0]["props"] or {}
    return {"name": name, "props": props}

def show_record(rec: dict, is_category: bool):
    name = rec.get("name", "")
    props = rec.get("props", {})

    st.markdown(f"## {name}")

    if is_category:
        st.write("Category — choose a subcategory below for full material data.")
        if props.get("synonyms"):
            st.write("**Synonyms:**", ", ".join(props["synonyms"]))
        return

    # --- material / process record (what EC3-style tools show) ---
    if props.get("region"):
        st.write("**Region:**", props["region"])
    if props.get("notes"):
        st.write("**Notes:**", props["notes"])
    if props.get("citation"):
        for c in props["citation"]:
            st.write(c)
    if props.get("synonyms"):
        st.write("**Synonyms:**", ", ".join(props["synonyms"]))
    if props.get("engineering"):
        st.write("**Engineering:**", props["engineering"])

    activity = props.get("activity")
    if isinstance(activity, dict):
        if activity.get("comment"):
            st.write("**Process:**", activity["comment"])
        ex = activity.get("exchanges") or {}
        if ex.get("technosphere"):
            st.markdown("**Inputs (technosphere)**")
            st.dataframe(ex["technosphere"], use_container_width=True)
        if ex.get("biosphere"):
            st.markdown("**Emissions (biosphere)**")
            st.dataframe(ex["biosphere"], use_container_width=True)

    # anything else stored on the node
    shown = {"region", "notes", "citation", "synonyms", "engineering", "activity"}
    extra = {k: v for k, v in props.items() if k not in shown}
    if extra:
        with st.expander("Other properties from database"):
            st.json(extra)

# -------- browse path (change any level; lower levels reset) --------

st.subheader("Browse materials")

if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

level = 0
name_by_id = {}
while True:
    options = get_root_nodes() if level == 0 else get_children(st.session_state.path_ids[level - 1])
    if not options:
        break

    ids = [o["id"] for o in options]
    name_by_id = {o["id"]: o["label"] for o in options}

    current = st.session_state.path_ids[level] if level < len(st.session_state.path_ids) else ids[0]
    if current not in ids:
        current = ids[0]

    picked = st.selectbox(
        f"Level {level + 1}",
        options=ids,
        index=ids.index(current),
        key=f"level_{level}",
        format_func=lambda x: name_by_id[x],
    )

    if level >= len(st.session_state.path_ids):
        st.session_state.path_ids.append(picked)
    elif st.session_state.path_ids[level] != picked:
        st.session_state.path_ids = st.session_state.path_ids[:level] + [picked]
        st.rerun()
    level += 1

st.session_state.path_ids = st.session_state.path_ids[:level]

if st.session_state.path:
    st.caption(" → ".join(name_by_id.get(i, "?") for i in st.session_state.path_ids))

if st.session_state.path_ids:
    node_id = st.session_state.path_ids[-1]
    rec = fetch_record(node_id)
    show_record(rec, is_category=has_children(node_id))
