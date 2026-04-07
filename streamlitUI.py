from neo4j import GraphDatabase
import streamlit as st

uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]
driver = GraphDatabase.driver(uri, auth=(user, password))

def run_query(q, params=None):
    with driver.session() as session:
        return [r.data() for r in session.run(q, params or {})]

st.title("AIF Graph Viewer 2")


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def get_root_options() -> list[dict]:
    rows = run_query(
        """
        MATCH (n:String)
        WHERE n.value IS NOT NULL
          AND trim(n.value) <> ""
          AND toLower(trim(n.value)) <> "null"
          AND NOT ()-[:HAS_CHILD]->(n)
        RETURN elementId(n) AS id, n.value AS value
        ORDER BY n.value
        """
    )
    return [
        {"id": row["id"], "value": clean_text(row["value"])}
        for row in rows
        if clean_text(row.get("value"))
    ]


def get_children(node_id: str) -> list[dict]:
    rows = run_query(
        """
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(p) = $node_id
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
          AND toLower(trim(c.value)) <> "null"
        RETURN elementId(c) AS id, c.value AS value
        ORDER BY c.value
        """,
        {"node_id": node_id},
    )
    return [
        {"id": row["id"], "value": clean_text(row["value"])}
        for row in rows
        if clean_text(row.get("value"))
    ]


def get_children_for_many(node_ids: list[str]) -> list[dict]:
    if not node_ids:
        return []

    rows = run_query(
        """
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(p) IN $node_ids
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
          AND toLower(trim(c.value)) <> "null"
        RETURN DISTINCT elementId(c) AS id, c.value AS value
        ORDER BY c.value
        """,
        {"node_ids": node_ids},
    )
    return [
        {"id": row["id"], "value": clean_text(row["value"])}
        for row in rows
        if clean_text(row.get("value"))
    ]


st.set_page_config(page_title="AIF Graph Viewer", layout="wide")

if "selected_path" not in st.session_state:
    st.session_state.selected_path = []

root_options = get_root_options()
root_labels = [item["value"] for item in root_options]

selected_root = st.selectbox(
    "",
    options=root_labels,
    index=None,
    placeholder="Select first item",
    key="level_0",
)

if selected_root:
    selected_root_node = next(
        (item for item in root_options if item["value"] == selected_root),
        None,
    )

    if selected_root_node:
        st.session_state.selected_path = [selected_root_node["id"]]

        level = 1
        current_parent_ids = [selected_root_node["id"]]

        while True:
            next_options = get_children_for_many(current_parent_ids)
            if not next_options:
                break

            next_labels = [item["value"] for item in next_options]
            selected_value = st.selectbox(
                "",
                options=next_labels,
                index=None,
                placeholder="Select next item",
                key=f"level_{level}",
            )

            if not selected_value:
                break

            selected_nodes = [
                item for item in next_options if item["value"] == selected_value
            ]
            selected_ids = [item["id"] for item in selected_nodes]

            if not selected_ids:
                break

            st.session_state.selected_path = st.session_state.selected_path[:level] + [
                selected_ids[0]
            ]
            current_parent_ids = selected_ids
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
