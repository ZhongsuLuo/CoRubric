"""Isolated legacy parser process used by folder import workers."""
import sys
from backend.app.services import convert_pdf

if __name__ == '__main__':
    convert_pdf(sys.argv[1], sys.argv[2], enable_audit=True)
