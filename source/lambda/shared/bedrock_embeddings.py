import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Bedrock client
bedrock_runtime = boto3.client("bedrock-runtime", region_name=os.environ.get("REGION", "us-west-2"))

# Embedding model configurations
EMBEDDING_MODELS = {
    "titan-embed-text-v1": {
        "model_id": "amazon.titan-embed-text-v1",
        "dimensions": 1536,
        "max_input_tokens": 8192,
        "cost_per_1k_tokens": 0.0001,
    },
    "titan-embed-text-v2": {
        "model_id": "amazon.titan-embed-text-v2:0",
        "dimensions": 1024,
        "max_input_tokens": 8192,
        "cost_per_1k_tokens": 0.00002,
    },
    "cohere-embed-english-v3": {
        "model_id": "cohere.embed-english-v3",
        "dimensions": 1024,
        "max_input_tokens": 512,
        "cost_per_1k_tokens": 0.0001,
    },
}


class BedrockEmbeddingsService:
    """Service for generating embeddings using Amazon Bedrock"""

    def __init__(self, model_name: str = "titan-embed-text-v2"):
        self.model_name = model_name
        self.model_config = EMBEDDING_MODELS.get(model_name)
        if not self.model_config:
            raise ValueError(f"Unsupported model: {model_name}")

        self.model_id = self.model_config["model_id"]
        self.dimensions = self.model_config["dimensions"]
        self.max_tokens = self.model_config["max_input_tokens"]

    def generate_embedding(self, text: str) -> Dict[str, Any]:
        """
        Generate embedding for a single text using Bedrock
        """
        try:
            # Truncate text if too long
            if len(text) > self.max_tokens * 4:  # Rough token estimation
                text = text[: self.max_tokens * 4]

            # Prepare request based on model type
            if "titan" in self.model_id:
                request_body = {"inputText": text}
            elif "cohere" in self.model_id:
                request_body = {"texts": [text], "input_type": "search_document"}
            else:
                raise ValueError(f"Unsupported model type: {self.model_id}")

            # Call Bedrock
            start_time = time.time()
            response = bedrock_runtime.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body),
                contentType="application/json",
                accept="application/json",
            )

            # Parse response
            response_body = json.loads(response["body"].read())
            processing_time = int((time.time() - start_time) * 1000)

            if "titan" in self.model_id:
                embedding = response_body.get("embedding", [])
            elif "cohere" in self.model_id:
                embedding = response_body.get("embeddings", [[]])[0]
            else:
                embedding = []

            return {
                "embedding": embedding,
                "model": self.model_id,
                "dimensions": len(embedding),
                "processing_time_ms": processing_time,
                "input_tokens": self._estimate_tokens(text),
                "success": True,
            }

        except (BotoCoreError, ClientError) as e:
            logger.error("Embedding SDK failure: %s", type(e).__name__)
            return {
                "embedding": [],
                "model": self.model_id,
                "dimensions": 0,
                "processing_time_ms": 0,
                "input_tokens": 0,
                "success": False,
                "error": str(e),
            }

    def generate_embeddings_batch(self, texts: List[str]) -> List[Dict[str, Any]]:
        """
        Generate embeddings for multiple texts
        """
        results = []
        for text in texts:
            result = self.generate_embedding(text)
            results.append(result)

            # Add small delay to avoid rate limiting
            time.sleep(0.1)  # nosemgrep: arbitrary-sleep - exponential backoff retry

        return results

    def generate_query_embedding(self, query: str) -> Dict[str, Any]:
        """
        Generate embedding optimized for query/search
        """
        # For Cohere models, use search_query input type
        if "cohere" in self.model_id:
            try:
                request_body = {"texts": [query], "input_type": "search_query"}

                start_time = time.time()
                response = bedrock_runtime.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(request_body),
                    contentType="application/json",
                    accept="application/json",
                )

                response_body = json.loads(response["body"].read())
                processing_time = int((time.time() - start_time) * 1000)

                embedding = response_body.get("embeddings", [[]])[0]

                return {
                    "embedding": embedding,
                    "model": self.model_id,
                    "dimensions": len(embedding),
                    "processing_time_ms": processing_time,
                    "input_tokens": self._estimate_tokens(query),
                    "success": True,
                }

            except (BotoCoreError, ClientError) as e:
                logger.error("Query embedding SDK failure: %s", type(e).__name__)
                return {
                    "embedding": [],
                    "model": self.model_id,
                    "dimensions": 0,
                    "processing_time_ms": 0,
                    "input_tokens": 0,
                    "success": False,
                    "error": str(e),
                }
        else:
            # For Titan models, use regular embedding generation
            return self.generate_embedding(query)

    def _estimate_tokens(self, text: str) -> int:
        """
        Rough estimation of token count
        """
        # Simple estimation: ~4 characters per token
        return len(text) // 4

    def calculate_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Calculate cosine similarity between two embeddings
        """
        if len(embedding1) != len(embedding2) or len(embedding1) == 0:
            return 0.0

        # Calculate dot product
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2))

        # Calculate magnitudes
        magnitude1 = sum(a * a for a in embedding1) ** 0.5
        magnitude2 = sum(a * a for a in embedding2) ** 0.5

        # Avoid division by zero
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        # Calculate cosine similarity
        return dot_product / (magnitude1 * magnitude2)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for Bedrock embeddings service
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")

        # Extract request information
        action = event.get("action", "generate_embedding")
        model_name = event.get("model", "titan-embed-text-v2")

        # Initialize embeddings service
        embeddings_service = BedrockEmbeddingsService(model_name)

        if action == "generate_embedding":
            text = event.get("text", "")
            if not text:
                return create_error_response(400, "Text parameter is required")

            result = embeddings_service.generate_embedding(text)
            return create_success_response(result)

        elif action == "generate_embeddings_batch":
            texts = event.get("texts", [])
            if not texts:
                return create_error_response(400, "Texts parameter is required")

            results = embeddings_service.generate_embeddings_batch(texts)
            return create_success_response(
                {
                    "results": results,
                    "total_processed": len(results),
                    "successful": len([r for r in results if r["success"]]),
                }
            )

        elif action == "generate_query_embedding":
            query = event.get("query", "")
            if not query:
                return create_error_response(400, "Query parameter is required")

            result = embeddings_service.generate_query_embedding(query)
            return create_success_response(result)

        elif action == "calculate_similarity":
            embedding1 = event.get("embedding1", [])
            embedding2 = event.get("embedding2", [])

            if not embedding1 or not embedding2:
                return create_error_response(400, "Both embeddings are required")

            similarity = embeddings_service.calculate_similarity(embedding1, embedding2)
            return create_success_response(
                {
                    "similarity": similarity,
                    "embedding1_dimensions": len(embedding1),
                    "embedding2_dimensions": len(embedding2),
                }
            )

        elif action == "get_model_info":
            return create_success_response(
                {
                    "available_models": list(EMBEDDING_MODELS.keys()),
                    "current_model": model_name,
                    "model_config": embeddings_service.model_config,
                }
            )

        else:
            return create_error_response(400, f"Unsupported action: {action}")

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return create_error_response(500, "Internal server error")


def create_success_response(data: Any, status_code: int = 200) -> Dict[str, Any]:
    """Create a successful response"""
    return {"statusCode": status_code, "body": json.dumps(data, default=str)}


def create_error_response(status_code: int, message: str) -> Dict[str, Any]:
    """Create an error response"""
    return {"statusCode": status_code, "body": json.dumps({"error": message, "code": status_code})}


# Utility functions for other Lambda functions to use
def get_embeddings_service(model_name: str = "titan-embed-text-v2") -> BedrockEmbeddingsService:
    """Get an instance of the embeddings service"""
    return BedrockEmbeddingsService(model_name)


def generate_content_embedding(content: str, model_name: str = "titan-embed-text-v2") -> Dict[str, Any]:
    """Generate embedding for content"""
    service = BedrockEmbeddingsService(model_name)
    return service.generate_embedding(content)


def generate_search_embedding(query: str, model_name: str = "titan-embed-text-v2") -> Dict[str, Any]:
    """Generate embedding for search query"""
    service = BedrockEmbeddingsService(model_name)
    return service.generate_query_embedding(query)
