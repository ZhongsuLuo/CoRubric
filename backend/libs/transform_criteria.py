import os
import json

from docx import Document

# 针对 .doc (旧版Word) 需要调用 Windows COM 接口
try:
    import win32com.client as win32
except ImportError:
    win32 = None

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from backend.libs.config import CRITERIA_TRANSFORM, get_chat_client

def extract_text_from_file(file_path: str) -> str:
    """Read content from .txt, .docx, .doc files"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
        
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()

    if ext == '.txt':
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    elif ext == '.docx':
        doc = Document(file_path)
        return '\n'.join([para.text for para in doc.paragraphs])
    elif ext == '.doc':
        if win32 is None:
            raise ImportError("pywin32 is required for .doc files")
        try:
            word = win32.Dispatch("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(os.path.abspath(file_path))
            content = doc.Content.Text
            doc.Close()
            return content
        except Exception as e:
            raise RuntimeError(f"Failed to read .doc: {e}")
    else:
        raise ValueError(f"Unsupported format: {ext}")

def criteria_to_json_file(
    input_path: str,
    output_dir: str,
    title: str = None, 
    background: str = None, 
    language: str = None, 
    base_score: int = None
):
    print(f"Processing: {input_path} ...")
    try:
        raw_text = extract_text_from_file(input_path)
    except Exception as e:
        print(f"Read Error: {e}")
        return

    # Prepare metadata strings
    user_title = f'"{title}"' if title else "None (Extract from text)"
    user_bg = f'"{background}"' if background else "None (Extract from text)"
    user_lang = f'"{language}"' if language else "None (Extract from text)"
    user_base = str(base_score) if base_score is not None else "None (Extract from text)"

    # --- System Prompt (Pure English) ---
    system_prompt = (
        "You are an expert educational data assistant. "
        "Your task is to convert raw grading criteria into a strict JSON format for an AI Autograder. "
        "The output must be detailed, self-contained, and strictly follow the provided schema. "
        "When a target Language is supplied, every human-readable output field must use that language."
    )

    # --- User Prompt (Pure English with Conditional Logic) ---
    user_prompt_content = f"""
# Task
Convert the "Criteria Text" into a structured JSON object.

# Input Data
Criteria Text:
\"\"\"
{raw_text}
\"\"\"

# Metadata Instructions
- Title: {user_title}
- Background: {user_bg}
- Language: {user_lang}
- Base Score: {user_base}

# Output Schema (Strict JSON)
{{
    "title": "String. Name of the criteria set.",
    "background": "String. Background context.",
    "language": "String. Language code (e.g., 'zh', 'en').",
    "base_score": "Number. Total score base.",
    "criteria": [
        {{
            "criteria_id": "Integer. Unique sequential integer (1, 2, 3...).",
            "description": "String. A detailed, self-contained grading instruction. See 'Description Logic' below.",
            "original_description": "String. Exact copy of the original text.",
            "max_score": "Integer. The absolute positive value of points.",
            "section": "String. Required non-empty semantic grading category name.",
            "few_shot_examples": []
        }}
    ]
}}

# CRITICAL Constraints

1. **Description Logic (Conditional)**:
   - **Case A: Specific Question Exists** (e.g., "Question 1", "3. Discussion").
     The description MUST follow this format:
     "[Label for Question]: [Original Question Text] \\n [Label for Requirement]: [Detailed answer keys and checks] \\n [Label for Scoring]: [Scoring/Deduction logic]"
   
   - **Case B: General Rule / No Question** (e.g., "Formatting", "Participation").
     The description MUST follow this format:
     "[Label for Requirement]: [Detailed checks] \\n [Label for Scoring]: [Scoring/Deduction logic]"

   *Note: Use labels appropriate for the source text language (e.g., use 'Question/Requirement' for English text; use '题目/要求' for Chinese text).*

2. **Self-Contained Content**: The description must contain ALL information needed to grade. Do not refer to external questions or keys without including them.

3. **Language Consistency**: When Metadata Language is supplied, title, background, criteria_name, description, section, scoring point summaries, and deduction conditions MUST use that language. Otherwise preserve the source language.

4. **Scoring Values**: "max_score" must always be a positive integer. If the text says "deduct 5 points", set max_score to 5.

5. **IDs**: "criteria_id" must be sequential integers (1, 2, 3...).

6. **Semantic Categories**: "section" describes what is being assessed, never where the content appears in a report. Do not use report headings such as Introduction, Principle, Results, Discussion, or Questions. Organize the complete rubric into a small coherent category set. Reuse category names across related criteria and avoid singleton categories whenever the number and meaning of criteria allow; normally each category should contain at least two criteria. Semantic coherence is more important than perfectly equal category sizes. Every criterion must have a non-empty section.

    """

    # Call Model
    try:
        transform_provider, transform_model = CRITERIA_TRANSFORM
        completion = get_chat_client(transform_provider).chat.completions.create(
            model=transform_model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt_content}
            ],
            temperature=0.1, 
            response_format={"type": "json_object"}
        )
        
        content = completion.choices[0].message.content
        
        # Clean markdown
        if content.startswith("```json"): content = content[7:]
        if content.endswith("```"): content = content[:-3]
        
        json_data = json.loads(content.strip())
        
        # Save file
        if not os.path.exists(output_dir): os.makedirs(output_dir)
        output_name = os.path.splitext(os.path.basename(input_path))[0] + ".json"
        output_full_path = os.path.join(output_dir, output_name)
        
        with open(output_full_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=4, ensure_ascii=False)
        print(f"Success: {output_full_path}")
        
    except Exception as e:
        transform_provider, transform_model = CRITERIA_TRANSFORM
        print(
            f"\n{'='*60}\n"
            f"[LLM ERROR] Criteria Transform | provider={transform_provider} model={transform_model}\n"
            f"  {type(e).__name__}: {e}\n"
            f"{'='*60}\n",
            file=sys.stderr, flush=True
        )
