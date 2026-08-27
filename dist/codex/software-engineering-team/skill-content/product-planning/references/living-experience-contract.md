# Living Experience References in the Backlog

Approved Experience packages are process-owned documents under
`workspace/docs/experience-design/experiences/<process-slug>/`.
Their only visual implementation is the globally current
`workspace/docs/experience-design/artifacts/application.html` identified by
`application@rN`. Each process package has one
`artifacts/application-map.json`; package-local previews and manifests are not
backlog inputs.

- Cite current work with an exact active child reference such as
  `checkout:SCR-001@r2`, never a package folder path or release identity.
- The Backlog compiler resolves new work through the owning approved/current
  Experience registry and requires the version-2 package map to cover that
  exact ref with an exact route/state entry in the pinned application. Frozen
  approved backlog revisions validate older refs and receipts through verified
  immutable ledgers so supersession or retirement does not deadlock a normal
  revision; replacement bindings remain strict-current.
- A Requirement-mode backlog receives its Experience receipt set from the
  Requirement Stage Results. It contains the globally current application
  receipt and exact current process receipts. A manual backlog pins the same
  complete set in compiler-owned input bindings.
- Package rename aliases preserve historical references. New work uses the
  current process slug. `application` is reserved and never names or aliases a
  process package.
- An approved process create, update, rename or retire action always produces a
  newer global application receipt. So does an independent application-only
  revision, without changing process receipts. A backlog bound to the previous
  application receipt must revise and rebind before further handoff.
- Final process retirement leaves a verified empty application receipt with
  zero process receipts. Requirement and backlog consumers may bind that
  receipt; the next process joins through a later application revision.
- Mechanical coverage establishes that the selected exact ref has a declared
  deep route. It does not establish that the rendered application is a faithful
  or usable expression of the record; that remains Experience review evidence.
- Backlog approval prepares Delivery Scope. It does not activate a delivery
  or reserve capacity.
