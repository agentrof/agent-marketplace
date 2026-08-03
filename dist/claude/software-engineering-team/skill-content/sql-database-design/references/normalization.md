# Normalization

## Normal Forms

- First Normal Form (1NF): atomic values only, no repeating groups, no nested structures. Every column holds a single value of a consistent type.
- Second Normal Form (2NF): 1NF plus no partial dependencies. Every non-key attribute depends on the entire composite primary key, not just part of it.
- Third Normal Form (3NF): 2NF plus no transitive dependencies. Non-key attributes depend only on the primary key, not on other non-key attributes.
- Boyce-Codd Normal Form (BCNF): every determinant is a candidate key. Stricter than 3NF; eliminates the remaining anomalies from functional dependencies.
- Fourth Normal Form (4NF): BCNF plus no multi-valued dependencies. Independent multi-valued facts about an entity live in separate tables.
- Fifth Normal Form (5NF): 4NF plus no join dependencies. The table cannot be reconstructed from smaller tables without data loss.

## Working Rules

- Start at 3NF (BCNF where cheap) for all transactional tables. This eliminates update, insert, and delete anomalies, which are correctness bugs waiting to happen.
- 4NF and 5NF matter mostly when a table encodes several independent many-to-many facts. If you spot two unrelated repeating facts in one table, split them.
- Normalization is about mutable data. Immutable event/log rows can carry redundant context freely because nothing ever drifts.

## Denormalization Discipline

Denormalize ONLY for a measured, high-ROI read path, and record it as an ADR (context / decision / alternatives / consequences). Premature denormalization creates a maintenance burden with no proven benefit.

Every denormalized copy of a mutable source field is a SNAPSHOT. The design MUST declare:

1. Refresh mechanism: DB trigger, application-side dual write, or batch job. "It gets updated somehow" is not a mechanism.
2. Staleness tolerance: how far behind the source the copy may lag before it is a bug (zero, seconds, one nightly cycle).

A copy that silently drifts from its source is a correctness defect, not a style issue. Prefer alternatives that keep a single source of truth:

- Materialized views: precomputed, refreshable, and clearly derived. Best default.
- Summary tables maintained by a scheduled job with a documented cadence.
- Covering indexes: often remove the join cost that motivated the denormalization in the first place.

Legitimate snapshot case: point-in-time capture. An order line storing `unit_price_at_purchase` is not denormalization; the business rule is that the value must NOT follow the source. Name such fields so the point-in-time semantic is explicit.

## Worked Decomposition

Unnormalized order sheet: `(order_id, customer_name, customer_email, product_code, product_name, quantity, unit_price)` with one row per product per order.

- 1NF: already atomic; ensure one product per row, no comma-joined lists.
- 2NF: `product_name` depends only on `product_code`, not on the full `(order_id, product_code)` key. Split out `products(product_code, product_name)`.
- 3NF: `customer_email` depends on the customer, not the order. Split out `customers(customer_id, name, email)`; orders reference `customer_id`.

Result: `customers`, `products`, `orders(order_id, customer_id, ...)`, `order_items(order_id, product_code, quantity, unit_price)`. Note `unit_price` stays on the order item as a deliberate point-in-time snapshot with no refresh (staleness tolerance: infinite, by business rule); the current price lives on `products`.
