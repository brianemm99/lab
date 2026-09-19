`curl -X POST -F "file=@progit.pdf" http://localhost:9000/upload`


Redirect users to my Docling video for better OCR


```curl -X POST \
  http://localhost:9000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is Git?",
    "pdf_id": "pdf_progit.pdf",
    "chat_history": []
  }'
  
  
```
curl -X GET "http://localhost:9000/pdfs/pdf_progit.pdf/chunks/101"