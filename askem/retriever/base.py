import json
import logging
import os

import weaviate
from data_models import Document
from fastapi import HTTPException


def get_client(url: str = None, apikey: str = None) -> weaviate.Client:
    """Get a weaviate client."""

    if url is None:
        url = os.getenv("WEAVIATE_URL")

    if apikey is None:
        apikey = os.getenv("WEAVIATE_APIKEY")

    logging.info(f"Connecting to Weaviate at {url}")
    return weaviate.Client(url, weaviate.auth.AuthApiKey(apikey))


def get_schema(class_name: str = "Passage") -> dict:
    """Obtain the v1 schema."""
    return {
        "class": class_name,
        "description": "Paragraph chunk of a document",
        "vectorizer": "text2vec-transformers",
        "moduleConfig": {"text2vec-transformers": {"vectorizeClassName": False}},
        "vectorIndexConfig": {"distance": "dot"},
        "properties": [
            {
                "name": "paper_id",
                "dataType": ["text"],
                "moduleConfig": {"text2vec-transformers": {"skip": True}},
            },
            {
                "name": "preprocessor_id",
                "dataType": ["text"],
                "moduleConfig": {"text2vec-transformers": {"skip": True}},
            },
            {
                "name": "doc_type",
                "dataType": ["text"],
                "moduleConfig": {"text2vec-transformers": {"skip": True}},
            },
            {
                "name": "cosmos_object_id",
                "dataType": ["text"],
                "moduleConfig": {"text2vec-transformers": {"skip": True}},
            },
            {
                "name": "topic_list",
                "dataType": ["text[]"],
                "moduleConfig": {"text2vec-transformers": {"skip": True}},
            },
            {
                "name": "hashed_text",
                "description": "SHA256 hash of text_content",
                "dataType": ["text"],
                "moduleConfig": {"text2vec-transformers": {"skip": True}},
            },
            {"name": "text_content", "dataType": ["text"]},
        ],
    }


def init_retriever(client: weaviate.Client | None = None) -> None:
    """Initialize the retriever."""

    if client is None:
        client = get_client()

    schema = get_schema()
    client.schema.create_class(schema)


def to_document(result: dict) -> Document:
    """Convert a weaviate result to a `Document`."""

    return Document(
        paper_id=result["paper_id"],
        topic=result["topic"],
        cosmos_object_id=result["cosmos_object_id"],
        doc_type=result["doc_type"],
        text=result["text_content"],
        distance=result["_additional"]["distance"],
    )


def get_documents(
    client: weaviate.Client,
    question: str,
    top_k: int = 5,
    distance: float | None = None,
    topic: str | None = None,
    doc_type: str | None = None,
    preprocessor_id: str | None = None,
    paper_ids: list[str] | None = None,
    move_to: str | None = None,
    move_to_weight: float | None = 1.0,
    move_away_from: str | None = None,
    move_away_from_weight: float | None = 1.0,
) -> list[Document]:
    """Ask a question to retriever and return a list of relevant `Document`.

    Args:
        client: Weaviate client.
        question: Query string.
        top_k: Number of documents to return. Defaults to 5.
        distance: Max distance of the document. Defaults to None.
        topic: Topic filter of the document. Defaults to None (No filter).
        doc_type: Doc type filter of the document. Defaults to None (No filter).
        preprocessor_id: Preprocessor filter of the document. Defaults to None (No filter).
        paper_ids: List of paper ids to filter by. Defaults to None (No filter).
        move_to: Adds an optional concept string to the query vector for more targeted results. Defaults to None, meaning no additional concept is added.
        move_to_weight: Weight of the move_to vectoring (range: 0-1). Defaults to 1.0.
        move_away_from: Adds an optional concept string to the query vector for more targeted results. Defaults to None, meaning no additional concept is added.
        move_away_from_weight: Weight of the move_away_from vectoring (range: 0-1). Defaults to 1.0.
    """

    output_fields = [
        "paper_id",
        "cosmos_object_id",
        "preprocessor_id",
        "topic",
        "doc_type",
        "text_content",
    ]

    # ========== Build query: filtering, semantic search, limit ==========
    results = client.query.get("Passage", output_fields).with_additional(["distance"])

    # Filtering
    where_filter = {"operator": "And", "operands": []}

    # by topic
    if topic is not None:
        logging.info(f"Filtering by topic: {topic}")
        where_filter["operands"].append(
            {"path": ["topic"], "operator": "Equal", "valueText": topic}
        )

    # by doc_type
    if doc_type is not None:
        logging.info(f"Filtering by doc_type: {doc_type}")
        where_filter["operands"].append(
            {"path": ["doc_type"], "operator": "Equal", "valueText": doc_type}
        )

    # by preprocessor id
    if preprocessor_id is not None:
        logging.info(f"Filtering by preprocessor_id: {preprocessor_id}")

        where_filter["operands"].append(
            {
                "path": ["preprocessor_id"],
                "operator": "Equal",
                "valueText": preprocessor_id,
            }
        )

    # by paper_ids
    if paper_ids is not None:
        logging.info(f"Filtering by paper_ids: {paper_ids}")

        where_filter["operands"].append(
            {
                "path": ["paper_id"],
                "operator": "ContainsAny",
                "valueText": paper_ids,
            }
        )

    if where_filter["operands"]:
        results = results.with_where(where_filter)

    # Semantic search

    near_text_query = {"concepts": [question]}

    if distance is not None:
        near_text_query["distance"] = distance

    logging.debug(f"{near_text_query=}")

    if move_to is not None:
        logging.debug(f"Moving towards {move_to} with weight {move_to_weight}")
        near_text_query["moveTo"] = {"concepts": [move_to], "force": move_to_weight}

    if move_away_from is not None:
        logging.debug(
            f"Moving away from {move_away_from} with weight {move_away_from_weight}"
        )
        near_text_query["moveAwayFrom"] = {
            "concepts": [move_away_from],
            "force": move_away_from_weight,
        }

    results = results.with_near_text(near_text_query)

    # Limit results and run
    results = results.with_limit(top_k).do()

    logging.debug(f"{results=}")

    # Check if errors occur
    if "errors" in results:
        raise HTTPException(status_code=500, detail=results["errors"])

    if "data" not in results or not results["data"]["Get"]["Passage"]:
        logging.info(f"No results found")
        logging.info(f"{results=}")
        raise HTTPException(status_code=404, detail=f"No results found: {results}")

    logging.info(f"Retrieved {len(results['data']['Get']['Passage'])} results")

    # Convert results to Document and return
    return [to_document(result) for result in results["data"]["Get"]["Passage"]]
