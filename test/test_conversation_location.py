from services.query_analyzer import analyze_query


history = [
    {
        "role": "user",
        "content": "I'm staying in RS Puram."
    }
]


question = "Find restaurants nearby."


result = analyze_query(
    question,
    history
)


print(result)