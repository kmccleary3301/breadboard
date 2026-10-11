use codex_apply_patch::{MaybeApplyPatchVerified, apply_patch, maybe_parse_apply_patch_verified, parse_patch, verify_apply_patch_args};
use codex_exec_server::LocalFileSystem;
use codex_utils_absolute_path::AbsolutePathBuf;
use codex_utils_output_truncation::{TruncationPolicy, formatted_truncate_text, truncate_text};
use serde::Deserialize;
use serde_json::json;
use std::io::{self, Read};
use std::time::Instant;

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum TruncationMode { Bytes, Tokens }

#[derive(Deserialize)]
struct ModelTruncationPolicy {
    mode: TruncationMode,
    limit: usize,
}

#[derive(Deserialize)]
struct Input {
    argv: Vec<String>,
    cwd: String,
    truncation_policy: ModelTruncationPolicy,
    spawn_error: Option<i32>,
    max_output_tokens: Option<usize>,
    direct_patch: Option<String>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let input: Input = serde_json::from_str(&input)?;
    let cwd = AbsolutePathBuf::try_from(input.cwd)?;
    let display = shlex::try_join(input.argv.iter().map(String::as_str))
        .unwrap_or_else(|_| "<command included NUL byte>".to_owned());
    if let Some(errno) = input.spawn_error {
        // process_manager.rs:987,1078; runtimes/unified_exec.rs:402;
        // handlers/unified_exec/exec_command.rs:303-305 render nested Debug.
        #[derive(Debug)]
        #[allow(dead_code)]
        enum Error {
            CreateProcess { message: String },
            Rejected(String),
        }
        let inner = format!("Failed to create unified exec process: {}", io::Error::from_raw_os_error(errno));
        let error = Error::CreateProcess { message: format!("{:?}", Error::Rejected(inner)) };
        println!("{}", json!({"intercepted": true, "error": true,
            "output": format!("exec_command failed for `{display}`: {error:?}")}));
        return Ok(());
    }
    let fs = LocalFileSystem::unsandboxed();
    let verified = if let Some(patch) = &input.direct_patch {
        // handlers/apply_patch.rs:332-360 parses the custom input directly.
        let args = match parse_patch(patch) {
            Ok(args) => args,
            Err(error) => {
                println!("{}", json!({"intercepted": true, "error": true,
                    "output": format!("apply_patch verification failed: {error}")}));
                return Ok(());
            }
        };
        verify_apply_patch_args(args, &cwd, &fs, None).await
    } else {
        maybe_parse_apply_patch_verified(&input.argv, &cwd, &fs, None).await
    };
    let output = match verified {
        MaybeApplyPatchVerified::Body(action) => {
            // core/tools/runtimes/apply_patch.rs:226-255; tools/mod.rs:62-86.
            let start = Instant::now();
            let mut stdout = Vec::new();
            let mut stderr = Vec::new();
            let result = apply_patch(&action.patch, &action.cwd, &mut stdout, &mut stderr, &fs, None).await;
            let elapsed = (start.elapsed().as_secs_f32() * 10.0).round() / 10.0;
            let content = format!("{}{}", String::from_utf8_lossy(&stdout), String::from_utf8_lossy(&stderr));
            // events.rs:344-349 passes the original byte/token turn policy.
            let policy = match input.truncation_policy.mode {
                TruncationMode::Bytes => TruncationPolicy::Bytes(input.truncation_policy.limit),
                TruncationMode::Tokens => TruncationPolicy::Tokens(input.truncation_policy.limit),
            };
            let policy_tokens = match input.truncation_policy.mode {
                TruncationMode::Bytes => input.truncation_policy.limit.div_ceil(4),
                TruncationMode::Tokens => input.truncation_policy.limit,
            };
            let truncated = truncate_text(&content, policy);
            let mut sections = vec![format!("Exit code: {}", if result.is_ok() { 0 } else { 1 }), format!("Wall time: {elapsed} seconds")];
            if content.lines().count() != truncated.lines().count() {
                sections.push(format!("Total output lines: {}", content.lines().count()));
            }
            sections.push("Output:".to_owned());
            sections.push(truncated);
            let inner = sections.join("\n");
            // Interception returns ExecCommandToolOutput with empty chunk/id/count,
            // zero wall time and no exit code: exec_command.rs:243-253.
            let text = if result.is_ok() && input.direct_patch.is_none() {
                format!("Wall time: 0.0000 seconds\nOutput:\n{}", formatted_truncate_text(&inner, TruncationPolicy::Tokens(input.max_output_tokens.unwrap_or(10_000).min(policy_tokens))))
            } else {
                inner
            };
            json!({"intercepted": true, "error": result.is_err(), "output": text})
        }
        MaybeApplyPatchVerified::CorrectnessError(error) => json!({
            "intercepted": true, "error": true,
            "output": format!("apply_patch verification failed: {error}")
        }),
        MaybeApplyPatchVerified::ShellParseError(_) | MaybeApplyPatchVerified::NotApplyPatch => json!({"intercepted": false, "command_for_display": display}),
    };
    println!("{output}");
    Ok(())
}
