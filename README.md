# 🌍 AI-Powered Travel & Local Places Assistant

An AI-powered travel assistant that combines **Retrieval-Augmented Generation (RAG)** with **location-aware live place search** to provide travel information and nearby restaurants, cafes, and hotels.

The application understands the user's intent, maintains location context across follow-up questions, retrieves information from travel documents using RAG, and searches for nearby places using live location data.

---

## 📌 Project Overview

Traditional travel applications usually separate travel information from local place discovery.

For example:

- One application provides information about tourist attractions.
- Another application helps users find restaurants.
- Users also need to repeatedly specify their location.

This project combines these capabilities into a single conversational assistant.

The assistant can:

- Answer travel-related questions from a knowledge base.
- Recommend attractions based on travel documents.
- Find nearby restaurants, cafes, and hotels.
- Understand locality and city context.
- Remember location information during a conversation.
- Handle follow-up questions such as:
  - "What about Gandhipuram?"
  - "What about cafes?"
- Provide distance information.
- Provide Google Maps and directions links.
- Handle invalid locations and unavailable services gracefully.

---

# ✨ Key Features

## 1. Retrieval-Augmented Generation (RAG)

The assistant uses RAG to answer questions from travel documents.

Travel documents are:

1. Loaded from text files.
2. Split into smaller chunks.
3. Converted into embeddings.
4. Stored in Chroma.
5. Retrieved based on semantic similarity.
6. Passed to Gemini for answer generation.

Example:

```text
User:
What are popular attractions in Ooty?

        ↓

Query Analyzer

        ↓

Router

        ↓

RAG

        ↓

Chroma Vector Database

        ↓

Relevant travel documents

        ↓

Gemini

        ↓

Answer