import os, subprocess, uuid, ray, time, pathlib, shutil

# This directory will be synced to all nodes by Ray and will serve as the
# parent for all container workspaces.
CONTAINERS_DIR = pathlib.Path(__file__).parent.resolve() / "containers"
CONTAINERS_DIR.mkdir(exist_ok=True)

def sandbox_env(image:str):
    """
    Helper to create the runtime_env for a gVisor sandbox.
    We are not using docker volume mounts anymore. Instead, the `working_dir`
    is specified at the job-level in `ray.init()`.
    """
    return {
        "docker": {
            "image": image,
            "runtime": "runsc",  # <<< gVisor
        }
    }

@ray.remote
class DevSandbox:
    def __init__(self, image:str, session_id:str):
        self.image = image
        self.session_id = session_id
        # The working_dir from ray.init() is the root of our sandbox.
        # We create a unique directory for this session inside it.
        self.cwd = f"./{self.session_id}"
        os.makedirs(self.cwd, exist_ok=True)

    def add_file(self, path: str, content: bytes):
        """Writes content to a file, creating parent dirs if needed."""
        # Note: we need to import this here as it's not available by default
        # in the python version used in the container.
        import pathlib

        full_path = pathlib.Path(self.cwd) / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    def run(self, cmd):
        """
        Execute a command and return all output lines as a list.
        This is not a streaming implementation, but it is more robust
        than the previous generator-based approach.
        """
        proc = subprocess.Popen(
            cmd,
            cwd=self.cwd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        lines = []
        for line in proc.stdout:
            lines.append(line.rstrip())
        return lines

    def get_cwd_contents(self):
        """Return the contents of the current working directory."""
        return os.listdir(self.cwd)

    def get_session_id(self):
        """Returns the unique session ID for this sandbox."""
        return self.session_id


if __name__ == "__main__":
    # The `working_dir` is specified here at the job level. Ray will sync the
    # contents of CONTAINERS_DIR to the main directory of the container on all nodes.
    ray.init(runtime_env={"working_dir": str(CONTAINERS_DIR)})

    TEMPLATES = {
        "python":  "python-dev:latest",
        "react":   "react-dev:latest",
        "c":       "gcc-dev:latest",
    }

    def get_sandbox(user, template):
        session_id = str(uuid.uuid4())
        actor_name = f"sandbox-{user}-{template}-{session_id}"
        env = sandbox_env(TEMPLATES[template])
        print(f"Creating sandbox for {user} with template {template}...")
        print(f"  Actor name: {actor_name}")
        print(f"  Session ID: {session_id}")

        # Pass the session_id to the actor's constructor.
        return DevSandbox.options(runtime_env=env, name=actor_name).remote(
            image=TEMPLATES[template], session_id=session_id
        )

    def cleanup_sandbox(actor):
        """Kills the actor, which implicitly cleans up its workspace."""
        if not actor:
            return
        
        session_id = ray.get(actor.get_session_id.remote())
        print(f"\nCleaning up sandbox {session_id}...")
        ray.kill(actor)
        print("  -> Actor killed. Workspace will be garbage collected by Ray.")

    # --- Test Python Sandbox ---
    print("--- Testing Python sandbox ---")
    python_sandbox = None
    try:
        python_sandbox = get_sandbox("test_user", "python")

        # First, a simple echo test
        print(">>> Running echo test...")
        # The run method now returns a list, not a generator.
        echo_output = ray.get(python_sandbox.run.remote("echo 'Hello from Python sandbox'"))
        for line in echo_output:
            print(line)
        assert echo_output == ["Hello from Python sandbox"]
        print("<<< Echo test complete.")

        # Now, test file persistence
        print(">>> Running file persistence test...")
        ray.get(python_sandbox.run.remote("echo 'import sys; print(sys.version)' > test.py"))

        print("\nListing workspace contents...")
        contents = ray.get(python_sandbox.get_cwd_contents.remote())
        print(f"  -> Contents: {contents}")
        assert "test.py" in contents
        print("✅ Persistence test passed.")

        print("\nReading file content...")
        output = ray.get(python_sandbox.run.remote("cat test.py"))
        print(f"  -> Output: {output}")
        assert "import sys" in output[0]
        print("✅ File read test passed.")
        
        print("\nExecuting python script...")
        output = ray.get(python_sandbox.run.remote("python test.py"))
        print(f"  -> Output: {output}")
        assert "3." in output[0]
        print("✅ Python script execution passed.")

        print("\nTesting file upload...")
        file_content = b"This is a test file upload."
        ray.get(python_sandbox.add_file.remote("new_dir/uploaded.txt", file_content))
        
        contents = ray.get(python_sandbox.get_cwd_contents.remote())
        print(f"  -> Workspace contents: {contents}")
        assert "new_dir" in contents

        output = ray.get(python_sandbox.run.remote("cat new_dir/uploaded.txt"))
        print(f"  -> Uploaded file content: {output}")
        assert output == ["This is a test file upload."]
        print("✅ File upload test passed.")

    except Exception as e:
        print(f"🚨 Python sandbox test failed: {e}")
    finally:
        cleanup_sandbox(python_sandbox)
        print("--- Python Test Complete ---")


    # --- Test React Sandbox ---
    print("\n--- Testing React sandbox ---")
    react_sandbox = None
    try:
        react_sandbox = get_sandbox("test_user", "react")

        # First, a simple echo test
        print(">>> Running echo test...")
        echo_output = ray.get(react_sandbox.run.remote("echo 'Hello from React sandbox'"))
        for line in echo_output:
            print(line)
        assert echo_output == ["Hello from React sandbox"]
        print("<<< Echo test complete.")

        # Now, test command execution
        print(">>> Running command execution test...")
        node_version_lines = ray.get(react_sandbox.run.remote("node -v"))
        print(f"  -> Output: {node_version_lines}")
        assert node_version_lines and node_version_lines[0].startswith("v")
        print("✅ Command execution complete.")

        print("\nExecuting javascript file...")
        output = ray.get(react_sandbox.run.remote(
            "echo \"console.log('Hello from Node.js script');\" > test.js && node test.js"
        ))
        print(f"  -> Output: {output}")
        assert output == ["Hello from Node.js script"]
        print("✅ Javascript execution passed.")

    except Exception as e:
        print(f"🚨 React sandbox test failed: {e}")
    finally:
        cleanup_sandbox(react_sandbox)
        print("--- React Test Complete ---")


    # --- Test C/GCC Sandbox ---
    print("\n--- Testing C/GCC sandbox ---")
    c_sandbox = None
    try:
        c_sandbox = get_sandbox("test_user", "c")

        # First, a simple echo test
        print(">>> Running echo test...")
        echo_output = ray.get(c_sandbox.run.remote("echo 'Hello from C sandbox'"))
        for line in echo_output:
            print(line)
        assert echo_output == ["Hello from C sandbox"]
        print("<<< Echo test complete.")

        # Now, test command execution
        print(">>> Compiling and running C code...")
        command = """
cat <<'EOF' > hello.c
#include <stdio.h>
int main() {
    printf("Hello from C!\\n");
    return 0;
}
EOF

gcc hello.c -o hello_exe && ./hello_exe
"""
        output = ray.get(c_sandbox.run.remote(command))
        print(f"  -> Output: {output}")
        assert output == ["Hello from C!"]
        print("✅ C code compilation and execution passed.")

    except Exception as e:
        print(f"🚨 C/GCC sandbox test failed: {e}")
    finally:
        cleanup_sandbox(c_sandbox)
        print("--- C/GCC Test Complete ---") 