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


def clean_options(values):
    return sorted({v for v in values if v not in (None, "", "null")})


def get_materials():
    rows = run_query("""
        MATCH (m:Material)
        RETURN m.material AS material
        ORDER BY material
    """)
    return clean_options([r.get("material") for r in rows])


def get_level1_options(material_name):
    if not material_name:
        return []

    rows = run_query("""
        MATCH (m:Material {material: $name})-[r]->()
        RETURN DISTINCT type(r) AS rel
        ORDER BY rel
    """, {"name": material_name})
    return clean_options([r.get("rel") for r in rows])


def get_next_options(material_name, selected_rels):
    if not material_name or not selected_rels:
        return []

    rows = run_query("""
        MATCH (m:Material {material: $name})-[r1]->(n)
        WHERE type(r1) IN $selected_rels
        MATCH (n)-[r2]->()
        RETURN DISTINCT type(r2) AS rel
        ORDER BY rel
    """, {
        "name": material_name,
        "selected_rels": selected_rels,
    })
    return clean_options([r.get("rel") for r in rows])


st.title("AIF Graph Viewer 2")
st.subheader("Dynamic Dropdowns")

materials = get_materials()

if materials:
    selected_material = st.selectbox("Material", materials, index=None, placeholder="Select material")
else:
    selected_material = None
    st.warning("No non-null materials found.")

level1_options = get_level1_options(selected_material)

if level1_options:
    level1_selected = st.multiselect("Level 1", level1_options)
else:
    level1_selected = []
    st.info("No non-null level 1 options.")

st.write("Checked boxes:", level1_selected)

level2_options = get_next_options(selected_material, level1_selected)

if level2_options:
    level2_selected = st.selectbox("Next dropdown", level2_options, index=None, placeholder="Select next option")
    st.write("Next selected:", level2_selected)
else:
    st.info("No non-null next options.")

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
