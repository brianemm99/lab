import json
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
import requests
import os
import time
import logging
import sys
from PyPDF2 import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from langchain_text_splitters import TokenTextSplitter
import aiohttp
from fastapi.middleware.cors import CORSMiddleware
import uuid

# Configure logging to write to stdout immediately
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Force flush stdout
sys.stdout.reconfigure(line_buffering=True)

app = FastAPI(title="PDF QA Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:*",
        "http://127.0.0.1:*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
qdrant_client = QdrantClient(
    host=os.getenv("QDRANT_HOST", "localhost"),
    port=int(os.getenv("QDRANT_PORT", "6333")),
)
EMBEDDINGS_SERVICE_URL = os.getenv("EMBEDDINGS_SERVICE_URL", "http://localhost:8080")
LOCALAI_URL = os.getenv("LOCALAI_URL", "http://localhost:8080/v1")


class QuestionRequest(BaseModel):
    question: str
    pdf_id: Optional[str] = None
    chat_history: Optional[List[Dict[str, str]]] = []


def format_phi_prompt(system_content: str, user_content: str) -> str:
    """Format prompt for Phi model with proper tokens"""
    return (
        f"<|system|>{system_content}<|end|><|user|>{user_content}<|end|><|assistant|>"
    )


def process_pdf(file_path: str) -> str:
    """Extract text from PDF using PyPDF2."""
    logger.info("📚 Starting PDF processing...")
    process_start = time.time()

    try:
        reader = PdfReader(file_path)
        text = ""

        # Extract text from each page
        for i, page in enumerate(reader.pages):
            logger.info(f"Processing page {i+1}/{len(reader.pages)}")
            text += page.extract_text() + "\n\n"

        process_time = time.time() - process_start

        if not text.strip():
            raise Exception("No text could be extracted from the PDF")

        logger.info(f"✨ Processing completed in {process_time:.2f}s")
        return text, {"process_time": process_time}

    except Exception as e:
        logger.error(f"❌ Error processing PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=f"PDF processing error: {str(e)}")


def get_embedding(text: str) -> List[float]:
    logger.info("🧮 Generating embedding for text...")
    response = requests.post(f"{EMBEDDINGS_SERVICE_URL}/embed", json={"text": text})
    if response.status_code != 200:
        logger.error("❌ Embedding service error")
        raise HTTPException(status_code=500, detail="Embedding service error")
    logger.info("✅ Embedding generated successfully")
    return response.json()


def get_embeddings_batch(texts: List[str]) -> List[List[float]]:
    logger.info(f"🔄 Generating embeddings for batch of {len(texts)} texts...")
    response = requests.post(
        f"{EMBEDDINGS_SERVICE_URL}/embed_batch", json={"texts": texts}
    )
    if response.status_code != 200:
        logger.error("❌ Batch embedding service error")
        raise HTTPException(status_code=500, detail="Embedding service error")
    logger.info("✅ Batch embeddings generated successfully")
    return response.json()


def setup_collection(collection_name: str, vector_size: int):
    logger.info(f"🗄️ Setting up Qdrant collection: {collection_name}")
    try:
        qdrant_client.get_collection(collection_name)
        logger.info("✅ Collection already exists")
    except:
        logger.info("💫 No existing collection found. Creating new one...")
        qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info("✅ Collection created successfully")


async def query_llm(prompt: str, context: str):
    logger.info("🤖 Querying LLM with context and question...")

    system_content = "You are a helpful assistant that answers questions based on the provided context. Do not provide anything other than the answer to the user's question. Answer based on the context."
    if context:
        system_content += f" Context: {context}"

    formatted_prompt = format_phi_prompt(system_content, prompt)

    data = {
        "model": "phi-3.5-mini-instruct",
        "prompt": formatted_prompt,
        "temperature": 0.3,
        "stop": ["<|endoftext|>", "<|end|>"],
        "stream": True,
        "max_tokens": 1024,
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{LOCALAI_URL}/completions", json=data
            ) as response:
                if response.status != 200:
                    logger.error(f"❌ LLM service error: {response.status}")
                    raise HTTPException(status_code=500, detail="LLM service error")

                # Process the streaming response
                async for line in response.content:
                    line = line.decode("utf-8").strip()

                    # Skip empty lines
                    if not line:
                        continue

                    # Handle SSE format
                    if line.startswith("data: "):
                        line = line[6:]

                    # Skip end marker
                    if line == "[DONE]":
                        continue

                    try:
                        chunk = json.loads(line)
                        if chunk.get("choices") and chunk["choices"][0].get("text"):
                            content = chunk["choices"][0]["text"]
                            yield content

                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse chunk: {line}")
                        continue

        except Exception as e:
            logger.error(f"❌ Error during streaming: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Streaming error: {str(e)}")

    logger.info("✅ LLM streaming completed successfully")


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    logger.info(f"📤 Processing upload request for file: {file.filename}")
    if not file.filename.endswith(".pdf"):
        logger.error("❌ Invalid file type")
        raise HTTPException(status_code=400, detail="File must be a PDF")

    temp_path = f"/tmp/{file.filename}"
    try:
        logger.info("💾 Saving uploaded file temporarily...")
        with open(temp_path, "wb") as temp_file:
            content = await file.read()
            temp_file.write(content)

        text, processing_stats = process_pdf(temp_path)

        logger.info("✂️ Splitting text into chunks...")
        text_splitter = TokenTextSplitter(
            # Larger chunks make the invocation linearly slower
            chunk_size=600,
        )
        chunks = text_splitter.split_text(text)
        logger.info(f"📋 Created {len(chunks)} chunks")

        logger.info("🔍 Getting sample embedding for vector size...")
        sample_embedding = get_embedding(chunks[0])
        vector_size = len(sample_embedding)

        collection_name = f"{file.filename}_{str(uuid.uuid4())}"
        setup_collection(collection_name, vector_size)

        logger.info("📥 Storing chunks in Qdrant...")
        batch_size = 100
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            logger.info(
                f"⏳ Processing batch {i//batch_size + 1}/{(len(chunks)-1)//batch_size + 1}"
            )
            embeddings = get_embeddings_batch(batch)

            points = [
                PointStruct(
                    id=i + j,
                    vector=embedding,
                    payload={
                        "text": text,
                        "metadata": {
                            "chunk_id": i + j,
                            "processing_stats": processing_stats,
                        },
                    },
                )
                for j, (text, embedding) in enumerate(zip(batch, embeddings))
            ]

            qdrant_client.upsert(collection_name=collection_name, points=points)

        logger.info("🎉 PDF processing completed successfully!")
        return {
            "pdf_id": collection_name,
            "message": "PDF processed successfully",
            "stats": {
                "chunks": len(chunks),
                "processing_time": processing_stats["process_time"],
            },
        }

    finally:
        if os.path.exists(temp_path):
            logger.info("🧹 Cleaning up temporary file...")
            os.remove(temp_path)


@app.post("/ask")
async def ask_question(request: QuestionRequest):
    logger.info(
        f"❓ Received question. PDF ID: {request.pdf_id if request.pdf_id else 'None'}"
    )

    # If no PDF is selected, directly query the LLM without context
    if not request.pdf_id:
        logger.info("📝 No PDF selected, proceeding with direct LLM query")

        async def generate_stream():
            try:
                # Send empty metadata since there's no context
                yield json.dumps(
                    {"type": "metadata", "context": "", "sources": []}
                ) + "\n"

                # Stream the answer chunks
                async for chunk in query_llm(request.question, ""):
                    yield json.dumps({"type": "chunk", "content": chunk}) + "\n"

            except Exception as e:
                logger.error(f"Error during streaming: {str(e)}")
                yield json.dumps({"type": "error", "content": str(e)}) + "\n"

        return StreamingResponse(generate_stream(), media_type="application/x-ndjson")

    # If PDF is selected, proceed with embedding search and context
    logger.info("🔍 Getting question embedding...")
    question_embedding = get_embedding(request.question)

    logger.info("🔎 Searching for relevant chunks...")
    results = qdrant_client.search(
        collection_name=request.pdf_id, query_vector=question_embedding, limit=2
    )
    logger.info(f"📑 Found {len(results)} relevant chunks")

    context = "\n\n".join([result.payload["text"] for result in results])
    sources = [
        {"chunk_id": result.payload["metadata"]["chunk_id"]} for result in results
    ]

    async def generate_stream():
        try:
            yield json.dumps(
                {"type": "metadata", "context": context, "sources": sources}
            ) + "\n"

            async for chunk in query_llm(request.question, context):
                yield json.dumps({"type": "chunk", "content": chunk}) + "\n"

        except Exception as e:
            logger.error(f"Error during streaming: {str(e)}")
            yield json.dumps({"type": "error", "content": str(e)}) + "\n"

    return StreamingResponse(generate_stream(), media_type="application/x-ndjson")


@app.get("/pdfs")
async def list_pdfs():
    """List all available PDF collections"""
    try:
        collections = qdrant_client.get_collections()
        pdf_collections = [
            {"id": collection.name, "name": collection.name}
            for collection in collections.collections
        ]
        return {"pdfs": pdf_collections}
    except Exception as e:
        logger.error(f"❌ Error listing PDFs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error listing PDFs: {str(e)}")


class ChunkResponse(BaseModel):
    """Response model for chunk retrieval"""

    chunk_id: int
    text: str
    metadata: dict


@app.get("/pdfs/{pdf_id}/chunks/{chunk_id}", response_model=ChunkResponse)
async def get_chunk(pdf_id: str, chunk_id: int):
    """
    Retrieve a specific chunk from a PDF collection by its ID.

    Args:
        pdf_id (str): The ID of the PDF collection (e.g., 'pdf_example_uuid.pdf')
        chunk_id (int): The ID of the specific chunk to retrieve

    Returns:
        ChunkResponse: Contains the chunk text and metadata

    Raises:
        HTTPException: If the PDF collection or chunk is not found
    """
    try:
        try:
            qdrant_client.get_collection(pdf_id)
        except Exception as e:
            logger.error(f"Collection {pdf_id} not found: {str(e)}")
            raise HTTPException(
                status_code=404, detail=f"PDF collection {pdf_id} not found"
            )

        results = qdrant_client.retrieve(
            collection_name=pdf_id,
            ids=[chunk_id],
        )

        if not results:
            logger.error(f"Chunk {chunk_id} not found in collection {pdf_id}")
            raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")

        chunk = results[0]

        return ChunkResponse(
            chunk_id=chunk_id,
            text=chunk.payload["text"],
            metadata=chunk.payload["metadata"],
        )

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Error retrieving chunk: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving chunk: {str(e)}")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    logger.info("🚀 Starting PDF QA Service...")
    uvicorn.run(app, host="0.0.0.0", port=9000, log_level="info", access_log=True)
