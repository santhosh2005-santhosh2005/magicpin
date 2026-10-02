"""
generate_submission.py
======================
Generates submission.jsonl from test_pairs.json and expanded dataset contexts.
"""

import json
from pathlib import Path
from bot import compose

def main():
    base_dir = Path(__file__).parent
    expanded_dir = base_dir / "dataset" / "expanded"
    
    test_pairs_path = expanded_dir / "test_pairs.json"
    if not test_pairs_path.exists():
        print(f"Error: {test_pairs_path} not found. Run dataset/generate_dataset.py first.")
        return

    with open(test_pairs_path, "r", encoding="utf-8") as f:
        test_pairs = json.load(f).get("pairs", [])

    # Load categories
    categories = {}
    cat_dir = expanded_dir / "categories"
    if cat_dir.exists():
        for cat_file in cat_dir.glob("*.json"):
            with open(cat_file, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                categories[cdata.get("slug", cat_file.stem)] = cdata

    # Load merchants
    merchants = {}
    m_dir = expanded_dir / "merchants"
    if m_dir.exists():
        for m_file in m_dir.glob("*.json"):
            with open(m_file, "r", encoding="utf-8") as f:
                mdata = json.load(f)
                merchants[mdata["merchant_id"]] = mdata

    # Load customers
    customers = {}
    c_dir = expanded_dir / "customers"
    if c_dir.exists():
        for c_file in c_dir.glob("*.json"):
            with open(c_file, "r", encoding="utf-8") as f:
                cdata = json.load(f)
                customers[cdata["customer_id"]] = cdata

    # Load triggers
    triggers = {}
    t_dir = expanded_dir / "triggers"
    if t_dir.exists():
        for t_file in t_dir.glob("*.json"):
            with open(t_file, "r", encoding="utf-8") as f:
                tdata = json.load(f)
                triggers[tdata["id"]] = tdata

    submission_rows = []
    
    for pair in test_pairs:
        test_id = pair["test_id"]
        t_id = pair["trigger_id"]
        m_id = pair["merchant_id"]
        c_id = pair.get("customer_id")
        
        trg = triggers.get(t_id, {"id": t_id, "kind": "generic", "payload": {}})
        merchant = merchants.get(m_id, {"merchant_id": m_id, "identity": {"name": "Merchant"}})
        cat_slug = merchant.get("category_slug", "dentists")
        category = categories.get(cat_slug, {"slug": cat_slug})
        customer = customers.get(c_id) if c_id else None
        
        composed = compose(category, merchant, trg, customer)
        
        row = {
            "test_id": test_id,
            "body": composed["body"],
            "cta": composed["cta"],
            "send_as": composed["send_as"],
            "suppression_key": composed["suppression_key"],
            "rationale": composed["rationale"]
        }
        submission_rows.append(row)

    out_path = base_dir / "submission.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for row in submission_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Successfully generated {len(submission_rows)} test outputs in {out_path}")

if __name__ == "__main__":
    main()
