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

from neo4j import GraphDatabase
import streamlit as st

uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]
driver = GraphDatabase.driver(uri, auth=(user, password))


def run_query(query, params=None):
    with driver.session() as session:
        return [r.data() for r in session.run(query, params or {})]


def clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def get_top_levels():
    rows = run_query("""
        MATCH (n:String)
        WHERE n.value IS NOT NULL
          AND trim(n.value) <> ""
          AND NOT (()-[:HAS_CHILD]->(n))
        RETURN elementId(n) AS id, n.value AS value
        ORDER BY value
    """)
    return [
        {"id": row["id"], "value": clean_text(row["value"])}
        for row in rows
        if clean_text(row.get("value"))
    ]


def get_children_of_node(node_id):
    if not node_id:
        return []

    rows = run_query("""
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(p) = $node_id
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
        RETURN elementId(c) AS id, c.value AS value
        ORDER BY value
    """, {"node_id": node_id})
    return [
        {"id": row["id"], "value": clean_text(row["value"])}
        for row in rows
        if clean_text(row.get("value"))
    ]


def get_children_of_checked_nodes(node_ids):
    if not node_ids:
        return []

    rows = run_query("""
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(p) IN $node_ids
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
        RETURN DISTINCT c.value AS value
        ORDER BY value
    """, {"node_ids": node_ids})
    values = []
    seen = set()
    for row in rows:
        value = clean_text(row.get("value"))
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values

top_nodes = get_top_levels()
top_labels = [node["value"] for node in top_nodes]

selected_top_label = st.selectbox(
    "Top Level",
    top_labels,
    index=None,
    placeholder="Select a top-level category",
)

selected_top_id = next(
    (node["id"] for node in top_nodes if node["value"] == selected_top_label),
    None,
)

child_nodes = get_children_of_node(selected_top_id)
child_labels = [node["value"] for node in child_nodes]

checked_child_labels = st.multiselect(
    "Check boxes",
    child_labels,
    key="checked_children",
)

checked_child_ids = [
    node["id"]
    for node in child_nodes
    if node["value"] in checked_child_labels
]

next_dropdown_options = get_children_of_checked_nodes(checked_child_ids)

selected_next = st.selectbox(
    "Next dropdown",
    next_dropdown_options,
    index=None,
    placeholder="Select next level",
)

st.write("Selected node:", selected_top_label)
st.write("Children of selected node:", child_labels)
st.write("Checked child nodes:", checked_child_labels)
st.write("Next-level options from checked child nodes only:", next_dropdown_options)


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
