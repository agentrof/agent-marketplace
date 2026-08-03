# Schema Validation

Document stores are schema-flexible, not schema-free. Every production collection carries a JSON Schema validator; flexibility is for evolution, not for skipping the contract.

## JSON Schema Validator

```javascript
db.createCollection("users", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["email", "name", "created_at"],
      properties: {
        email: {
          bsonType: "string",
          pattern: "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$",
          description: "must be a valid email"
        },
        name: {
          bsonType: "string",
          minLength: 1,
          maxLength: 200,
          description: "user display name"
        },
        age: {
          bsonType: "int",
          minimum: 0,
          maximum: 150,
          description: "must be a non-negative integer"
        },
        status: {
          enum: ["active", "inactive", "suspended"],
          description: "must be one of the allowed statuses"
        },
        created_at: {
          bsonType: "date",
          description: "document creation timestamp"
        }
      }
    }
  },
  validationLevel: "strict",
  validationAction: "error"
})
```

## Validation Levels

| Level | Behavior |
|-------|----------|
| `strict` | All inserts and updates must pass validation |
| `moderate` | Only documents that already match the schema are validated on update |

Use `moderate` only during a migration window while old-shape documents still exist; return to `strict` when the migration completes.

## Validation Actions

| Action | Behavior |
|--------|----------|
| `error` | Reject invalid documents (required for production) |
| `warn` | Accept invalid documents but log a warning (migration windows only) |

## Rules

- Every enum-like string field gets an `enum` constraint.
- Every required field appears in `required`; optionality is a decision, not a default.
- Pattern-constrain formats the business depends on (email, slug, currency code).
- Polymorphic collections use conditional schemas keyed on the `type` discriminator.
- Keep the validator versioned alongside the schema-versioning pattern: when `schema_version` advances, the validator advances with it.
