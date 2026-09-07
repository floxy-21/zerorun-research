# Temporary credential cleanup

After the single recorded live model turn stopped, the temporary VM-only file
`/home/floxy/.codex/auth.json` was checked to be a regular non-symlink at that
exact resolved path, removed, and its absence verified by the terminal response
`TEMPORARY_VM_AUTH_REMOVED` on 7 September 2026 UTC. No repository, authority
directory, host login cache, or account was deleted or logged out. The user's
original desktop authentication remains untouched.

The copied authentication file was never included in an evidence export.
Any further diagnostic in this extension is non-model and requires no account
credentials. The preserved model receipt reports a failed bounded lifecycle;
it is not replaced with a successful rerun.
