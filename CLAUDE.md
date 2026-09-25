# Project Instructions

## Important Rules

- Do not repeat explanations or code that has already been provided unless I ask for it.
- Before making changes, inspect the existing implementation.
- Make the minimum required changes.
- Do not rewrite working code unnecessarily.
- Do not change the existing Gemini model or Gemini integration unless explicitly requested.
- Do not modify unrelated features.
- Preserve the existing RAG, Chroma, Places search, routing, conversation context, and error handling.
- Do not hardcode data, locations, document content, or API responses.
- Never expose API keys or secrets.
- Do not modify `.env` with real credentials.
- When modifying code, explain only the files that actually changed.
- Avoid repeating the entire project structure or previously explained implementation.
- If a requirement is unclear, ask before making a large architectural change.

## Current Project

This is a Travel & Local Places Assistant using:

- Streamlit
- FastAPI
- Gemini
- Chroma
- RAG
- Nominatim
- Geoapify
- Conversation context
- User-uploaded document RAG

The application uses Gemini as the LLM. Claude Code is only being used as a coding assistant and must not be added as an application LLM unless explicitly requested.

## Development Style

- Prefer small, focused changes.
- Reuse existing functions and services where possible.
- Keep existing functionality working.
- Do not create duplicate implementations when an existing service can be extended.
- Before creating a new file, check whether the functionality can be added cleanly to an existing file.