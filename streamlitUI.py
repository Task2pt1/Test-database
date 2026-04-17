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


# ----------------------------
# ROOT + CHILDREN (STRUCTURE)
# ----------------------------

def get_root_nodes():
    return run_query("""
        MATCH (n:String)
        WHERE NOT ()-[:HAS_CHILD]->(n)
        RETURN elementId(n) AS id, n.value AS label
        ORDER BY label
    """)


def get_children(node_id: str):
    return run_query("""
        MATCH (n:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(n) = $id
        RETURN elementId(c) AS id, c.value AS label
        ORDER BY label
    """, {"id": node_id})


# ----------------------------
# NODE VALUES (NOT STRUCTURE)
# ----------------------------

def get_node_values(node_id: str):
    # properties
    props = run_query("""
        MATCH (n)
        WHERE elementId(n) = $id
        RETURN properties(n) AS props
    """, {"id": node_id})

    props = props[0]["props"] if props else {}

    # detail nodes
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
# DROPDOWN RENDER
# ----------------------------

def render_dropdown(label, options, key):
    if not options:
        return None

    ids = [o["id"] for o in options]
    labels = {o["id"]: o["label"] for o in options}

    return st.selectbox(
        label,
        options=ids,
        index=None,
        key=key,
        format_func=lambda x: labels[x]
    )


# ----------------------------
# SESSION STATE
# ----------------------------

if "path" not in st.session_state:
    st.session_state.path = []


# ----------------------------
# MAIN LOOP
# ----------------------------

st.subheader("Browse Hierarchy")

level = 0
nodes = get_root_nodes()

while True:

    selected = render_dropdown(f"Level {level+1}", nodes, f"level_{level}")

    if selected is None:
        break

    # store path
    if len(st.session_state.path) > level:
        st.session_state.path[level] = selected
    else:
        st.session_state.path.append(selected)

    st.session_state.path = st.session_state.path[:level+1]

    # ----------------------------
    # NODE VALUES DROPDOWN
    # ----------------------------

    props, details = get_node_values(selected)

    st.markdown(f"### Level {level+1} Values")

    value_options = []

    # add properties as options
    for k, v in props.items():
        value_options.append({
            "label": k,
            "data": v
        })

    # add detail nodes
    value_options.extend(details)

    if value_options:
        selected_value = st.selectbox(
            f"Select value for Level {level+1}",
            options=range(len(value_options)),
            index=None,
            key=f"value_{level}",
            format_func=lambda i: value_options[i]["label"]
        )

        if selected_value is not None:
            st.json(value_options[selected_value]["data"])

    # ----------------------------
    # NEXT LEVEL (ONLY CHILDREN)
    # ----------------------------

    nodes = get_children(selected)
    level += 1
