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

def run_query(query, params=None):
    with driver.session() as session:
        return [r.data() for r in session.run(query, params or {})]


def clean_options(values):
    seen = set()
    result = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() == "null":
            continue
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def get_top_levels():
    rows = run_query("""
        MATCH (n:String)
        WHERE n.value IS NOT NULL
          AND trim(n.value) <> ""
          AND NOT (()-[:HAS_CHILD]->(n))
        RETURN n.value AS category
        ORDER BY category
    """)
    return clean_options([r.get("category") for r in rows])


def get_children(parent_value):
    if not parent_value:
        return []

    rows = run_query("""
        MATCH (p:String {value: $parent_value})-[:HAS_CHILD]->(c:String)
        WHERE c.value IS NOT NULL
          AND trim(c.value) <> ""
        RETURN DISTINCT c.value AS child
        ORDER BY child
    """, {"parent_value": parent_value})
    return clean_options([r.get("child") for r in rows])


def get_next_level_from_checked(checked_values):
    if not checked_values:
        return []

    rows = run_query("""
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE p.value IN $checked_values
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
        RETURN DISTINCT c.value AS child
        ORDER BY child
    """, {"checked_values": checked_values})
    return clean_options([r.get("child") for r in rows])


top_levels = get_top_levels()

if not top_levels:
    st.error("No top-level categories found.")
    st.stop()

selected_top_level = st.selectbox(
    "Top Level",
    top_levels,
    index=None,
    placeholder="Select a top-level category",
)

level1_options = get_children(selected_top_level)

level1_checked = st.multiselect(
    "Check boxes",
    level1_options,
    key="level1_checked",
)

next_dropdown_options = get_next_level_from_checked(level1_checked)

selected_next = st.selectbox(
    "Next dropdown",
    next_dropdown_options,
    index=None,
    placeholder="Select next level",
)

st.write("Top level selected:", selected_top_level)
st.write("Checked boxes:", level1_checked)
st.write("Next dropdown selected:", selected_next)
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
