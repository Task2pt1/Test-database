import streamlit as st
from neo4j import GraphDatabase
from typing import Any


uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]

driver = GraphDatabase.driver(uri, auth=(user, password))

st.set_page_config(page_title="AIF Graph Viewer 2", layout="wide")
st.title("AIF Graph Viewer 2")


def run_query(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with driver.session() as session:
        return [record.data() for record in session.run(query, params or {})]


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def normalize_nodes(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for row in rows:
        node_id = row.get("id")
        value = clean_text(row.get("value"))
        if node_id and value:
            items.append({"id": str(node_id), "value": value})
    return items


def get_root_nodes() -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (n:String)
        WHERE NOT ()-[:HAS_CHILD]->(n)
          AND n.value IS NOT NULL
          AND trim(n.value) <> ""
          AND toLower(trim(n.value)) <> "null"
        RETURN elementId(n) AS id, n.value AS value
        ORDER BY n.value, elementId(n)
        """
    )
    return normalize_nodes(rows)


def get_children(parent_id: str) -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(p) = $parent_id
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
          AND toLower(trim(c.value)) <> "null"
        RETURN elementId(c) AS id, c.value AS value
        ORDER BY c.value, elementId(c)
        """,
        {"parent_id": parent_id},
    )
    return normalize_nodes(rows)


def get_full_node_payload(node_id: str) -> dict:
    props = run_query(
        """
        MATCH (n)
        WHERE elementId(n) = $node_id
        RETURN properties(n) AS props
        """,
        {"node_id": node_id},
    )

    rels = run_query(
        """
        MATCH (n)-[r]->(m)
        WHERE elementId(n) = $node_id
        RETURN
            type(r) AS rel,
            elementId(m) AS id,
            labels(m) AS labels,
            properties(m) AS props
        ORDER BY rel
        """,
        {"node_id": node_id},
    )

    return {
        "properties": props[0]["props"] if props else {},
        "relationships": rels
    }


def render_dropdown(level: int, nodes: list[dict[str, str]]) -> str | None:
    option_ids = [node["id"] for node in nodes]
    labels = {node["id"]: node["value"] for node in nodes}

    saved_id = (
        st.session_state.path_ids[level]
        if level < len(st.session_state.path_ids)
        else None
    )

    if saved_id not in option_ids:
        saved_id = None

    index = option_ids.index(saved_id) if saved_id is not None else None

    return st.selectbox(
        f"Level {level + 1}",
        options=option_ids,
        index=index,
        placeholder="Select",
        key=f"path_{level}",
        format_func=lambda node_id: labels[node_id],
    )


# session state
if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

st.subheader("Browse Hierarchy")

level = 0
nodes = get_root_nodes()

while True:
    if not nodes:
        st.session_state.path_ids = st.session_state.path_ids[:level]
        break

    selected_id = render_dropdown(level, nodes)

    if selected_id is None:
        st.session_state.path_ids = st.session_state.path_ids[:level]
        break

    # store path
    if len(st.session_state.path_ids) > level:
        st.session_state.path_ids[level] = selected_id
    else:
        st.session_state.path_ids.append(selected_id)

    st.session_state.path_ids = st.session_state.path_ids[: level + 1]

    # SHOW EVERYTHING FOR THIS NODE
    st.markdown(f"### Level {level + 1} Data")

    payload = get_full_node_payload(selected_id)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Properties**")
        st.json(payload["properties"])

    with col2:
        st.markdown("**Relationships / Specs**")
        st.json(payload["relationships"])

    # continue ONLY along selected path
    nodes = get_children(selected_id)
    level += 1


st.subheader("Database Controls")

colA, colB = st.columns(2)

with colA:
    if st.button("Count Nodes"):
        total = run_query("MATCH (n) RETURN count(n) AS total")
        st.write(total)

with colB:
    if st.button("Show Full Database"):
        data = run_query(
            """
            MATCH (n)
            OPTIONAL MATCH (n)-[r]->(m)
            RETURN n, r, m
            LIMIT 500
            """
        )
        st.write(data)
