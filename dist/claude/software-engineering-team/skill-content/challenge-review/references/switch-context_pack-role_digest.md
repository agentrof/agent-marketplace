# Role Digest

These are the instructions of process switch `context_pack` at
`role_digest`. A task binds this file only when the project's Process
Policy selects that value; at the default, `off`, a role reads every flow,
skill and reference file its task manifest binds before it acts. Every task
of an owning flow binds it.

1. The orchestrating entry builds each spawned role's pack with
   `context_pack.py build --entry <entry> --role <role> --mode <mode>
   --project-root <root>` and gives it instead of the full required reads.
   The pack is derived from the bound sources, never authored, and names the
   `sha256` of each source.
2. A pack whose source hashes do not match the bound sources is stale: the
   role reads the full sources and says so in its output.
3. A role reads a named source in full only when the pack does not cover its
   case, and records that read and why. Every bound source stays one read
   away; the pack never narrows what a role may read.
4. The constitution, the role file and the switch instructions in force
   apply in full whatever the pack holds.

Record per task the minutes before the first write or finding and every
source read beyond the pack with its reason.
