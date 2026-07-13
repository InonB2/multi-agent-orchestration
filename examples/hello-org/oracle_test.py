from hello_org import canonical_member_id


# Product invariant: IDs use Unicode caseless normalization, not ASCII-style lowercasing.
assert canonical_member_id("  Straße  ") == "strasse", (
    "Unicode casefold defect: Straße must canonicalize to strasse"
)
print("ORACLE_TESTS_PASS")
