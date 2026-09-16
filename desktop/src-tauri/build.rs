fn main() {
    // The Supabase project is baked in at compile time (see sidecar.rs), so a
    // change to either value has to trigger a rebuild.
    println!("cargo:rerun-if-env-changed=SUPABASE_URL");
    println!("cargo:rerun-if-env-changed=SUPABASE_ANON_KEY");
    tauri_build::build()
}
