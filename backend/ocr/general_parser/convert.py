"""Production adapter for the evaluated parser. No benchmark samples or audit labels."""
import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf as fitz
from backend.ocr.common import IMAGE_RE, sha256, write_json
from backend.ocr.configuration import load_config
from backend.ocr.providers import parse_docmind
from backend.ocr.general_parser.cloud import Cloud
from backend.ocr.general_parser.core import local_page, VERSION
from backend.ocr.general_parser.rendering import assemble
from backend.ocr.hybrid_layout import render_region


def save_progress(path, **state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def convert(pdf, output, config, progress, retry=False):
    output.mkdir(parents=True, exist_ok=True)
    work = output.parent
    cache = work / 'base_ocr'
    cache.mkdir(exist_ok=True)
    signature = cache / 'source.sha256'
    digest = sha256(pdf)
    if signature.exists() and signature.read_text() != digest:
        raise ValueError('源文件已改变，不能复用原解析缓存')
    signature.write_text(digest)
    with fitz.open(pdf) as doc:
        total = len(doc)
        progress(stage='基础OCR', pages_done=0, pages_total=total)
        if not (cache / 'complete.json').exists():
            if (cache / 'submit_started.json').exists() and not (cache / 'job.json').exists():
                raise ValueError('上次OCR提交结果未知，请先核实云端任务，避免重复计费')
            result = parse_docmind(pdf, cache, config, enhanced=False,
                                   resume=(cache / 'job.json').exists(), page_count=total)
            write_json(cache / 'complete.json', result)
        status = json.loads((cache / 'status.json').read_text())
        layouts = json.loads((cache / 'layouts.json').read_text())
        pages = []
        for index, page in enumerate(doc):
            progress(stage=f'整理版面 {index + 1}/{total}', pages_done=0, pages_total=total)
            folder = output / 'pages' / f'p{index + 1:04d}'
            (folder / 'images').mkdir(parents=True, exist_ok=True)
            blocks = local_page(page, [b for b in layouts if b['pageNum'] == index], status)
            for block in blocks:
                block['source_image'] = f'pages/p{index + 1:04d}/source.png'
                if block['action'] in {'vision', 'asset'}:
                    block['image'] = f"pages/p{index + 1:04d}/images/{block['id']}.png"
                    render_region(doc, {**block, 'page': index + 1}, output / block['image'],
                                  config['hybrid']['dpi'], config['hybrid']['max_pixels'])
            if any(b['action'] == 'unresolved' for b in blocks):
                page.get_pixmap(dpi=120, alpha=False).save(folder / 'source.png')
            pages.append(blocks)
    tasks = [b for blocks in pages for b in blocks if b['action'] == 'vision']
    # Per-document, persistent safety cap; benchmark's global ledger is never used.
    cloud = Cloud(config, work / 'vision_cache', request_limit=500,
                  budget_cny=config.get('runtime', {}).get('vision_budget_cny_per_document', 9.5))
    try:
        if retry:
            cloud.prepare_retry()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}
            for block in tasks:
                reference = '原生文字层候选：\n' + block['native_hint'] if block.get('native_hint') else block.get('text', '')
                futures[pool.submit(cloud.call, output / block['image'], reference)] = block
            for done, future in enumerate(as_completed(futures), 1):
                futures[future]['result'] = future.result()
                progress(stage=f'图像增强 {done}/{len(tasks)}', pages_done=0, pages_total=total)
        parts = []
        unresolved = 0
        for index, blocks in enumerate(pages, 1):
            unresolved += sum(b['action'] == 'unresolved' or
                              b['action'] == 'vision' and (b.get('result', {}).get('status') != 'complete'
                              or any(i.get('uncertain') for i in b.get('result', {}).get('data', {}).get('items', [])))
                              for b in blocks)
            write_json(output / 'pages' / f'p{index:04d}' / 'manifest.json', blocks)
            parts.append(assemble(blocks, vision=True))
            progress(stage=f'保存Markdown {index}/{total}', pages_done=index, pages_total=total)
        text = '\n\n'.join(parts)
        if unresolved:
            (output / f'{pdf.stem}.partial.md').write_text(text, encoding='utf-8')
            raise ValueError(f'还有{unresolved}个区域未通过解析检查；已保留源图和中间结果，未加入评分列表')
        for path in output.glob('*.partial*'):
            path.unlink()
        (output / f'{pdf.stem}.md').write_text(text, encoding='utf-8')
        (output / f'{pdf.stem}_text_only.md').write_text(IMAGE_RE.sub('', text), encoding='utf-8')
        records = list(cloud.ledger.values())
        write_json(output / 'conversion.json', {'parser': VERSION, 'pages': total,
                   'vision_regions': len(tasks), 'api_requests': len(records),
                   'vision_estimated_cny': sum(r.get('estimated_cny') or 0 for r in records),
                   'unknown_requests': sum(r.get('estimated_cny') is None for r in records)})
        progress(stage='完成', pages_done=total, pages_total=total)
    finally:
        cloud.close()


def main():
    parser = argparse.ArgumentParser()
    for name in ('pdf', 'output', 'progress', 'config'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--retry', action='store_true')
    args = parser.parse_args()
    try:
        convert(args.pdf, args.output, load_config(args.config),
                lambda **s: save_progress(args.progress, **s), retry=args.retry)
    except Exception as exc:
        # Do not expose provider response bodies or credentials to the browser.
        message = str(exc) if isinstance(exc, ValueError) else f'解析失败（{type(exc).__name__}），请检查服务配置或重试'
        save_progress(args.progress, stage='失败', error=message)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
