"""Document processing - parse and chunk files"""
import re
from pathlib import Path
from typing import List, Tuple
import pypdf


class DocumentProcessor:
    """Load documents and split into chunks"""
    
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    
    def load_pdf(self, file_path: str) -> str:
        """Extract text from PDF"""
        text = ""
        with open(file_path, 'rb') as f:
            for page in pypdf.PdfReader(f).pages:
                text += page.extract_text() + "\n"
        return text
    
    def load_text(self, file_path: str) -> str:
        """Load text file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    def load_document(self, file_path: str) -> str:
        """Load PDF or TXT file"""
        suffix = Path(file_path).suffix.lower()
        if suffix == ".pdf":
            return self.load_pdf(file_path)
        elif suffix == ".txt":
            return self.load_text(file_path)
        else:
            raise ValueError(f"Unsupported format: {suffix}")
    
    def chunk_text(self, text: str) -> List[str]:
        """Split text into overlapping chunks"""
        text = re.sub(r'\s+', ' ', text).strip()
        chunks = []
        start = 0
        
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunks.append(text[start:end])
            start = end - self.chunk_overlap if end < len(text) else len(text)
        
        return chunks
    
    def process_document(self, file_path: str) -> List[Tuple[str, dict]]:
        """Parse document into chunks with metadata"""
        text = self.load_document(file_path)
        chunks = self.chunk_text(text)
        filename = Path(file_path).name
        
        return [(chunk, {"source": filename, "chunk_id": i}) for i, chunk in enumerate(chunks)]
    
    def process_directory(self, directory: str) -> List[Tuple[str, dict]]:
        """Process all PDF/TXT files in a directory"""
        all_chunks = []
        for file_path in Path(directory).rglob('*'):
            if file_path.suffix.lower() in {'.pdf', '.txt'}:
                all_chunks.extend(self.process_document(str(file_path)))
        return all_chunks
