from neo4j import GraphDatabase
import streamlit as st

uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]
driver = GraphDatabase.driver(uri, auth=(user, password))

st.title("AIF Graph Viewer 2")


def run_query(query: str, params: dict | None = None) -> list[dict]:
    with driver.session() as session:
        return [record.data() for record in session.run(query, params or {})]


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def get_root_nodes() -> list[dict]:
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
    return [
        {"id": row["id"], "value": clean_text(row["value"])}
        for row in rows
        if clean_text(row.get("value"))
    ]


def get_children(parent_id: str) -> list[dict]:
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
    return [
        {"id": row["id"], "value": clean_text(row["value"])}
        for row in rows
        if clean_text(row.get("value"))
    ]


def render_dropdown(level: int, nodes: list[dict]) -> str | None:
    option_ids = [node["id"] for node in nodes]
    labels = {node["id"]: node["value"] for node in nodes}
    saved_id = st.session_state.path_ids[level] if level < len(st.session_state.path_ids) else None

    if saved_id not in option_ids:
        saved_id = None

    return st.selectbox(
        "",
        options=option_ids,
        index=option_ids.index(saved_id) if saved_id is not None else None,
        placeholder="Select",
        key=f"path_{level}",
        label_visibility="collapsed",
        format_func=lambda node_id: labels[node_id],
    )


if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

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

    if len(st.session_state.path_ids) > level:
        st.session_state.path_ids[level] = selected_id
    else:
        st.session_state.path_ids.append(selected_id)

    st.session_state.path_ids = st.session_state.path_ids[: level + 1]

    nodes = get_children(selected_id)
    level += 1



    
# --- BUTTON 1: count nodes ---
if st.button("Count Nodes"):
    res = run_query("MATCH (n:String) RETURN count(n) AS total")
    st.write(res)

# --- BUTTON 2: select node ---
nodes = [r["value"] for r in run_query(
    "MATCH (n:String) RETURN n.value AS value ORDER BY value LIMIT 100"
)]

selected = st.selectbox("Select node", nodes)

# --- BUTTON 3: show children ---
if st.button("Show Children"):
    res = run_query("""
    MATCH (p:String {value:$name})-[:HAS_CHILD]->(c)
    RETURN c.value AS child
    """, {"name": selected})
    st.write(res)

# --- BUTTON 4: show connections ---
if st.button("Inspect Node"):
    res = run_query("""
    MATCH (n:String {value:$name})-[r]-(m)
    RETURN n.value AS node, type(r) AS rel, m.value AS connected
    """, {"name": selected})
    st.write(res)
    
if st.button("Show Top-Level Categories"):
    res = run_query("""
    MATCH (n:String)
    WHERE NOT ( ()-[:HAS_CHILD]->(n) )
    RETURN n.value AS category
    ORDER BY category
    """)
    st.write(res)
    
if st.button("Load Top-Level Categories"):
    st.session_state["roots"] = [
        r["category"] for r in run_query("""
        MATCH (n:String)
        WHERE NOT ( ()-[:HAS_CHILD]->(n) )
        RETURN n.value AS category
        ORDER BY category
        """)
    ]

# --- BUTTON: show full database ---
if st.button("Show Full Database"):
    data = run_query("""
    MATCH (n:String)
    OPTIONAL MATCH (n)-[:HAS_CHILD]->(c:String)
    RETURN n.value AS parent, collect(c.value) AS children
    ORDER BY parent
    """)

    st.subheader("Full Graph (Table)")
    st.dataframe(data)

    # --- build tree ---
    tree = {row["parent"]: row["children"] for row in data}

    # --- find roots ---
    roots = run_query("""
    MATCH (n:String)
    WHERE NOT ( ()-[:HAS_CHILD]->(n) )
    RETURN n.value AS root
    """)
    roots = [r["root"] for r in roots]

    # --- recursive display ---
    def show_tree(node, level=0):
        st.write("  " * level + "• " + node)
        for child in tree.get(node, []):
            if child:
                show_tree(child, level + 1)

    st.subheader("Hierarchy View")

    for r in roots:
        show_tree(r)
        
# --- BUTTON: show hierarchy ---
if st.button("Show Full Hierarchy"):

    # get parent → children
    data = run_query("""
    MATCH (n:String)
    OPTIONAL MATCH (n)-[:HAS_CHILD]->(c:String)
    RETURN n.value AS parent, collect(c.value) AS children
    ORDER BY parent
    """)

    tree = {row["parent"]: row["children"] for row in data}

    # find roots
    roots = run_query("""
    MATCH (n:String)
    WHERE NOT ( ()-[:HAS_CHILD]->(n) )
    RETURN n.value AS root
    """)
    roots = [r["root"] for r in roots]

    # recursive tree display
    def show_tree(node, level=0):
        st.markdown(f"{'&nbsp;&nbsp;&nbsp;' * level}• {node}", unsafe_allow_html=True)
        for child in tree.get(node, []):
            if child:
                show_tree(child, level + 1)

    st.subheader("Hierarchy")

    for r in roots:
        show_tree(r)
