# Worked Examples

Illustrative shapes using generic SQL; adjust spellings to the project's declared engine. Placeholder data only (John Doe, Acme Corp).

## Users (reference entity)

```sql
CREATE TABLE users (
  user_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email       text        NOT NULL,
  full_name   text        NOT NULL,
  is_active   boolean     NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_users_email UNIQUE (email),
  CONSTRAINT ck_users_email CHECK (email ~ '^[^@]+@[^@]+$')
);
CREATE UNIQUE INDEX uidx_users_email_lower ON users (lower(email));
```

Decisions shown: identity PK, `text` with a CHECK instead of bounded varchar, timezone-aware timestamps, case-insensitive uniqueness via an expression index, audit timestamps by default.

## Orders and order items (composition + snapshot)

```sql
CREATE TABLE orders (
  order_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     bigint      NOT NULL,
  status      text        NOT NULL DEFAULT 'pending',
  total       numeric(12,2) NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_orders_users_user_id FOREIGN KEY (user_id)
    REFERENCES users (user_id) ON DELETE RESTRICT,
  CONSTRAINT ck_orders_status CHECK (status IN ('pending','paid','canceled')),
  CONSTRAINT ck_orders_positive_total CHECK (total >= 0)
);
CREATE INDEX idx_orders_user_id ON orders (user_id);
CREATE INDEX idx_orders_user_id_created_at ON orders (user_id, created_at);

CREATE TABLE order_items (
  order_id       bigint        NOT NULL,
  product_id     bigint        NOT NULL,
  quantity       integer       NOT NULL CHECK (quantity > 0),
  unit_price_at_purchase numeric(12,2) NOT NULL,
  PRIMARY KEY (order_id, product_id),
  CONSTRAINT fk_order_items_orders_order_id FOREIGN KEY (order_id)
    REFERENCES orders (order_id) ON DELETE CASCADE,
  CONSTRAINT fk_order_items_products_product_id FOREIGN KEY (product_id)
    REFERENCES products (product_id) ON DELETE RESTRICT
);
CREATE INDEX idx_order_items_product_id ON order_items (product_id);
```

Decisions shown: RESTRICT by default, CASCADE only on the true composition (order owns its items), FK columns indexed manually, composite PK on the junction-style child, evolving status as text + CHECK rather than a native enum, and `unit_price_at_purchase` as an explicit point-in-time snapshot (refresh mechanism: none by business rule; staleness tolerance: infinite; recorded as a design decision).

## Profiles (semi-structured attributes)

```sql
CREATE TABLE profiles (
  user_id  bigint PRIMARY KEY,
  attrs    jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(attrs) = 'object'),
  theme    text GENERATED ALWAYS AS (attrs->>'theme') STORED,
  CONSTRAINT fk_profiles_users_user_id FOREIGN KEY (user_id)
    REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE INDEX idx_profiles_attrs ON profiles USING GIN (attrs);
CREATE INDEX idx_profiles_theme ON profiles (theme);
```

Decisions shown: one-to-one composition (profile dies with its user), JSON reserved for optional variable attributes with its shape constrained, inverted index for containment queries, and the frequently filtered scalar (`theme`) promoted to a stored generated column with a B-tree index instead of querying inside the document.
