from hello_org import canonical_member_id


assert canonical_member_id("  ALICE  ") == "alice"
assert canonical_member_id("Product-Team") == "product-team"
print("VISIBLE_TESTS_PASS")
