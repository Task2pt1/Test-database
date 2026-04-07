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

# --- materials ---
materials = [r["material"] for r in run_query(
    "MATCH (m:Material) RETURN m.material AS material ORDER BY material"
)]

selected_material = st.selectbox("Material", materials)

# --- LEVEL 1 (YOU WERE MISSING THIS) ---
lvl1_options = [
    "id", "code", "database", "placement", "vector",
    "synonyms", "comment", "citation", "standards", "region",
    "engineering", "activity", "lcia", "cost"
]

lvl1_selected = st.multiselect("Level 1", lvl1_options)

# --- MAIN LOOP ---
for key in lvl1_selected:

    st.subheader(key)

    # STEP 1: go from Material → selected key
    res = run_query(f"""
    MATCH (m:Material {{material: $name}})
    MATCH (m)-[:HAS_{key.upper()}]->(n)
    RETURN n
    """, {"name": selected_material})

    # if nothing exists, skip
    if not res:
        st.write("No data")
        continue

    # STEP 2: from THAT node → what relationships exist
    res2 = run_query(f"""
    MATCH (m:Material {{material: $name}})
    MATCH (m)-[:HAS_{key.upper()}]->(n)
    MATCH (n)-[r]->()
    RETURN DISTINCT type(r) AS rel
    """, {"name": selected_material})

    options = [r["rel"].replace("HAS_", "").lower() for r in res2]

    lvl2_selected = st.multiselect(f"{key} → next", options)

    # STEP 3: go deeper
    for nxt in lvl2_selected:

        res3 = run_query(f"""
        MATCH (m:Material {{material: $name}})
        MATCH (m)-[:HAS_{key.upper()}]->(n1)
        MATCH (n1)-[:HAS_{nxt.upper()}]->(n2)
        RETURN n2
        """, {"name": selected_material})

        st.write(f"{key} → {nxt}", res3)

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
