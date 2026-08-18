from langchain.tools import tool
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import FAQ_PDF_PATH, GEMINI_API_KEY

loader = PyPDFLoader(str(FAQ_PDF_PATH))
docs = loader.load()

@tool
def faq_retriever(question: str) -> str:
      """Busca na FAQ oficial os trechos mais relevantes para responder a pegunta"""
      splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=150)
      chunks = splitter.split_documents(docs)
      
      embbedings = GoogleGenerativeAIEmbeddings(
            model="gemini-embedding-2-preview",
            google_api_key=GEMINI_API_KEY,
      )
      
      db = FAISS.from_documents(chunks, embbedings)
      results = db.similarity_search(question, k=6)
      
      return "\n\n".join([result.page_content for result in results])

FAQ_TOOLS = [faq_retriever]