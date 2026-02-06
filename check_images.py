from openpyxl import load_workbook

f = "Company list.xlsx"
print(f"Checking {f} for images...")
try:
    wb = load_workbook(f)
    ws = wb.active
    print(f"  Images in active sheet: {len(ws._images)}")
    for img in ws._images:
        print(f"    Image: {img}")
except Exception as e:
    print(f"  ERROR: {e}")
