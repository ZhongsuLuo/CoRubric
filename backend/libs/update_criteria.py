import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import json

from backend.libs.config import CRITERIA_OPTIMIZE, get_chat_client


def _strip_json_fence(content: str) -> str:
    value = (content or "").strip()
    if value.startswith("```json"):
        value = value[7:]
    elif value.startswith("```"):
        value = value[3:]
    if value.endswith("```"):
        value = value[:-3]
    return value.strip()


def _parse_optimizer_response(content: str):
    cleaned = _strip_json_fence(content)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"description": cleaned}


def get_language_name(lang_code):
    """辅助函数：将语言代码转换为英文全称，用于 Prompt 指令"""
    lang_map = {
        "zh": "Chinese",
        "cn": "Chinese",
        "zh-cn": "Chinese",
        "en": "English",
        "en-us": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "ja": "Japanese",
        "ko": "Korean",
        "ru": "Russian",
    }
    return lang_map.get(lang_code.lower(), lang_code)

def optimize_rubric_with_examples(criteria_json_path, output_path=None):
    """
    核心函数：读取评分标准 -> 针对有 Examples 的项调用大模型 -> 重写 Description -> 保存
    """
    print(f"🚀 Starting Optimization for: {criteria_json_path}")
    
    if not os.path.exists(criteria_json_path):
        print(f"Error: File not found {criteria_json_path}")
        return None

    with open(criteria_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 获取目标语言（例如 'zh' -> 'Chinese'）
    target_lang_code = data.get("language", "zh")
    target_lang_name = get_language_name(target_lang_code)
    
    # 遍历每一项评分标准
    criteria_list = data.get("criteria", [])
    optimized_count = 0

    for item in criteria_list:
        few_shots = item.get("few_shot_examples", [])
        
        # 只有当存在 few-shot 且数量 > 0 时才需要优化
        if not few_shots:
            continue
            
        c_id = item.get('criteria_id')
        print(f"   -> Optimizing Criterion ID {c_id} using {len(few_shots)} examples...")

        # 1. 构建 Examples 文本块
        examples_text = ""
        for idx, ex in enumerate(few_shots, 1):
            examples_text += f"--- Example {idx} ---\n"
            examples_text += f"Input: {ex.get('example_input')}\n"
            examples_text += f"Score: {ex.get('example_output_score')} (Max: {item.get('max_score')})\n"
            examples_text += f"Reason: {ex.get('example_output_description')}\n\n"

        # 2. System Prompt: 设定专家角色
        system_prompt = (
            "You are an expert Curriculum Designer and Assessment Specialist. "
            "Your task is to refine a grading rule by incorporating specific logic derived from grading examples (Few-Shot). "
            "The criterion may include structured scoring_points with deduction rules. "
            "The goal is to make both the overall description and structured scoring points explicitly cover the edge cases shown in the examples."
        )

        # 3. User Prompt: 要求重写
        # 注意：这里要求输出必须使用 target_lang_name 指定的语言
        user_prompt = f"""
# Task
Refine this grading criterion so the scoring logic is explicit and structured.
Update the overall description and the per-point structured deduction rules.

# Context
- Target Language for Output: {target_lang_name} (CRITICAL: Output MUST be in this language)
- Criteria ID: {item.get('criteria_id')}
- Criteria Name: {item.get('criteria_name')}
- Max Score: {item.get('max_score')}

# Current Description
{json.dumps(item.get('description'), ensure_ascii=False)}

# Current Structured Scoring Points
{json.dumps(item.get('scoring_points'), ensure_ascii=False, indent=2)}

# Current Scoring Aggregation
{json.dumps(item.get('scoring_aggregation'), ensure_ascii=False, indent=2)}

# Reference Examples (The Ground Truth)
{examples_text}

# Instructions for Rewriting
1. Analyze the examples to identify specific deduction triggers, success criteria, score bands, and edge cases.
2. Preserve criteria_id. Do NOT split this criterion into multiple criteria.
3. Keep description as an overall human-readable summary.
4. Put concrete scoring details into scoring_points. A single criterion may contain multiple scoring_points.
5. Use stable point_id/rule_id strings when possible; add new IDs only for new scoring logic.
6. Set each deduction rule as: rule_id, condition, deduct.
7. Use scoring_aggregation to describe how deductions combine and how the total deduction is capped.
8. Output ONLY a strict JSON object with exactly these top-level keys: description, scoring_points, scoring_aggregation. No markdown fences, no explanations.
"""

        try:
            opt_provider, opt_model = CRITERIA_OPTIMIZE
            completion = get_chat_client(opt_provider).chat.completions.create(
                model=opt_model,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ],
                temperature=0.1 # 低温以保持严谨
            )
            
            parsed = _parse_optimizer_response(completion.choices[0].message.content or "")

            if isinstance(parsed.get("description"), str) and parsed["description"].strip():
                item["description"] = parsed["description"].strip()
            if isinstance(parsed.get("scoring_points"), list):
                item["scoring_points"] = parsed["scoring_points"]
            if isinstance(parsed.get("scoring_aggregation"), dict):
                item["scoring_aggregation"] = parsed["scoring_aggregation"]

            optimized_count += 1
            
        except Exception as e:
            opt_provider, opt_model = CRITERIA_OPTIMIZE
            print(
                f"\n{'='*60}\n"
                f"[LLM ERROR] Criteria Optimize | provider={opt_provider} model={opt_model}\n"
                f"  criterion: {c_id}  {type(e).__name__}: {e}\n"
                f"{'='*60}\n",
                file=sys.stderr, flush=True
            )

    # 4. 保存新的评分标准
    if output_path is None:
        base, ext = os.path.splitext(criteria_json_path)
        output_path = f"{base}_optimized{ext}"
        
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    
    print(f"✅ Optimization Complete. {optimized_count} items refined.")
    print(f"💾 Saved to: {output_path}")
    return output_path

# --- 测试运行 ---
if __name__ == "__main__":
    optimize_rubric_with_examples("backend/data/case/case 1/criteria/AAS评分标准_new.json", output_path="backend/data/case/case 1/criteria/AAS评分标准_V1.json")
