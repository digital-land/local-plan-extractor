#!/usr/bin/env python3
"""
Add documentation-url field to documents in existing source JSON files.

For each document in the documents array, adds a "documentation-url" field
after the "document-url" field. The value is taken from the top-level
"documentation-url" field, which represents the webpage where the documents
were found.
"""

import json
from pathlib import Path
from collections import OrderedDict


def add_documentation_url_to_documents(data):
    """Add documentation-url to each document in the documents array."""
    for plan in data:
        # Get the top-level documentation-url (the page where documents were found)
        top_level_doc_url = plan.get("documentation-url", "")

        # Update each document in the documents array
        if "documents" in plan and isinstance(plan["documents"], list):
            for doc in plan["documents"]:
                # Create a new ordered dict to maintain field order
                new_doc = OrderedDict()
                for key, value in doc.items():
                    new_doc[key] = value
                    # Insert documentation-url after document-url
                    if key == "document-url" and "documentation-url" not in doc:
                        new_doc["documentation-url"] = top_level_doc_url

                # Update the document with the new ordered dict
                doc.clear()
                doc.update(new_doc)

    return data


def main():
    source_dir = Path("source")

    # Find all local-authority JSON files
    json_files = sorted(source_dir.glob("local-authority:*.json"))

    print(f"Found {len(json_files)} source files to update")

    for json_file in json_files:
        print(f"\nProcessing {json_file.name}...", end=" ")

        try:
            # Read the file
            with open(json_file, "r") as f:
                data = json.load(f)

            # Add documentation-url fields
            updated_data = add_documentation_url_to_documents(data)

            # Write back to file
            with open(json_file, "w") as f:
                json.dump(updated_data, f, indent=2)

            print("✓ Updated")

        except Exception as e:
            print(f"✗ Error: {e}")

    print("\n✓ All files processed")


if __name__ == "__main__":
    main()
