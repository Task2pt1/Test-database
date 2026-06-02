import os
import sys
from neo4j import GraphDatabase

def main() -> None:
    uri = os.environ["NEO4J_URI"]
    user = os.environ["NEO4J_USERNAME"]
    password = os.environ["NEO4J_PASSWORD"]

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            ok = session.run("RETURN 1 AS ok").single()["ok"]
        print(f"Neo4j keep-alive OK: {ok}")
    finally:
        driver.close()

if __name__ == "__main__":
    main()
