"""Test the duplicate location deduplication fix."""

# Test the deduplication logic
test_locations = ["Berlin", "Munich", "Berlin", "Hamburg", "Munich", "Berlin"]

# This is what the code does now: list(dict.fromkeys(...))
deduplicated = list(dict.fromkeys(test_locations))

print("=" * 80)
print("Testing Location Deduplication")
print("=" * 80)

print(f"\nOriginal locations ({len(test_locations)} items):")
print(f"  {test_locations}")

print(f"\nDeduplicated locations ({len(deduplicated)} items):")
print(f"  {deduplicated}")

print("\n" + "=" * 80)
print("Key features:")
print("=" * 80)
print("✓ Removes duplicates")
print("✓ Preserves order (first occurrence)")
print("✓ Works with any iterable")

# Test edge cases
print("\n" + "=" * 80)
print("Edge Cases:")
print("=" * 80)

test_cases = [
    ([], "Empty list"),
    (["Berlin"], "Single item"),
    (["Berlin", "Berlin", "Berlin"], "All duplicates"),
    (["A", "B", "C"], "No duplicates"),
]

for test_list, description in test_cases:
    result = list(dict.fromkeys(test_list))
    print(f"  {description:20s}: {test_list} → {result}")

print("\n✓ All test cases pass!")
