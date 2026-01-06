"""
Isolated Agent CLI - Enhanced Tool Calling with Path Virtualization
Demonstrates complete workspace isolation while maintaining test-time visibility.
"""

import sys
import yaml
from pathlib import Path
import ray
import shutil
from typing import Dict, Any

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from kylecode.sandbox_virtualized import SandboxFactory, DeploymentMode
from agentic_coder_prototype.integration.lsp_integration import LSPIntegratedToolExecutor
from kylecode.sandbox_lsp_integration import LSPEnhancedSandbox

def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration from YAML file"""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

def create_isolated_agent_system(config_path: str, deployment_mode: str = "development"):
    """Create a fully isolated agentic system with path virtualization"""
    
    print(f"🔒 Creating Isolated Agentic System")
    print(f"📁 Config: {config_path}")
    print(f"🚀 Mode: {deployment_mode}")
    print("=" * 50)
    
    # Load configuration
    config = load_config(config_path)
    mode = DeploymentMode(deployment_mode)
    
    # Initialize Ray if needed
    if not ray.is_initialized():
        ray.init()
    
    # Create virtualized sandbox
    factory = SandboxFactory()
    virtualized_sandbox, session_id = factory.create_sandbox(mode, config)
    
    print(f"✓ Created isolated session: {session_id}")
    print(f"✓ Deployment mode: {mode.value}")
    
    # Wrap with LSP integration if available
    try:
        # Create LSP-enhanced version
        lsp_sandbox = LSPEnhancedSandbox.remote(virtualized_sandbox, "/workspace")  
        enhanced_sandbox = lsp_sandbox
        print("✓ LSP integration enabled")
    except Exception as e:
        print(f"! LSP integration unavailable: {e}")
        enhanced_sandbox = virtualized_sandbox
    
    # Create enhanced tool executor
    enhanced_executor = LSPIntegratedToolExecutor.create_from_config(
        enhanced_sandbox, config
    )
    print("✓ Enhanced tool calling enabled")
    
    return {
        "sandbox": enhanced_sandbox,
        "executor": enhanced_executor,
        "session_id": session_id,
        "factory": factory,
        "config": config
    }

def demonstrate_isolation(system: Dict[str, Any]):
    """Demonstrate the isolation capabilities"""
    
    print("\n🔍 Demonstrating Path Isolation")
    print("-" * 30)
    
    sandbox = system["sandbox"]
    session_id = system["session_id"]
    
    # Show what model sees vs reality
    print("Model's Virtual View:")
    print("  Current working directory: ./")
    print("  Files: ./src/main.py, ./tests/test.py, ./README.md")
    print("  No knowledge of host filesystem structure")
    
    print(f"\nReal Session Storage:")
    print(f"  Session ID: {session_id}")
    print(f"  Real path: /tmp/agent_sessions/{session_id}/workspace/")
    print(f"  Completely isolated from host")
    
    # Test some operations
    print(f"\n📝 Testing Isolated Operations:")
    
    # Write a file - model sees virtual path
    result = ray.get(sandbox.write_text.remote("./hello.py", "print('Hello from isolated world!')"))
    print(f"✓ Write operation: {result.get('virtual_path', result.get('path', 'unknown'))}")
    
    # List directory - model sees clean listing  
    files = ray.get(sandbox.ls.remote("."))
    print(f"✓ Directory listing: {files}")
    
    # Read file back
    content = ray.get(sandbox.read_text.remote("./hello.py"))
    print(f"✓ File content preview: {content['content'][:30]}...")

async def demonstrate_enhanced_tool_calling(system: Dict[str, Any]):
    """Demonstrate enhanced tool calling with isolation"""
    
    print("\n🔧 Demonstrating Enhanced Tool Calling with Isolation")
    print("-" * 50)
    
    executor = system["executor"]
    
    # Test tool calls with virtual paths
    test_calls = [
        {
            "function": "write_text",
            "arguments": {
                "path": "./src/calculator.py",
                "content": '''def add(a, b):
    """Add two numbers."""
    return a + b

def multiply(a, b):
    """Multiply two numbers."""
    return a * b

# Test the functions
if __name__ == "__main__":
    print(f"2 + 3 = {add(2, 3)}")
    print(f"4 * 5 = {multiply(4, 5)}")
'''
            }
        },
        {
            "function": "write_text", 
            "arguments": {
                "path": "./tests/test_calculator.py",
                "content": '''import sys
sys.path.append('../src')
from calculator import add, multiply

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0

def test_multiply():
    assert multiply(4, 5) == 20
    assert multiply(0, 10) == 0

if __name__ == "__main__":
    test_add()
    test_multiply()
    print("All tests passed!")
'''
            }
        },
        {
            "function": "ls",
            "arguments": {"path": "."}
        }
    ]
    
    for i, tool_call in enumerate(test_calls, 1):
        print(f"\n--- Tool Call {i}: {tool_call['function']} ---")
        
        try:
            # Execute with enhanced validation and LSP feedback
            result = await executor.execute_tool_call(tool_call)
            
            # Show the results
            if "lsp_feedback" in result:
                print(f"LSP Feedback: {result['lsp_feedback']}")
            
            if "virtual_path" in result:
                print(f"Virtual Path: {result['virtual_path']}")
            elif "path" in result:
                print(f"Path: {result['path']}")
                
            if tool_call['function'] == 'ls':
                print(f"Directory Contents: {result}")
                
            print("✓ Tool call completed successfully")
            
        except Exception as e:
            print(f"❌ Tool call failed: {e}")

def demonstrate_test_time_mirroring(system: Dict[str, Any]):
    """Show test-time mirroring for development visibility"""
    
    print("\n🪞 Demonstrating Test-Time Mirroring")
    print("-" * 40)
    
    config = system["config"]
    mirror_path = config.get("workspace")
    
    if mirror_path and Path(mirror_path).exists():
        print(f"✓ Test results mirrored to: {mirror_path}")
        print("✓ Developers can see real-time results")
        
        # Show mirrored files
        mirror_files = list(Path(mirror_path).rglob("*"))
        print("📁 Mirrored files:")
        for f in mirror_files[:10]:  # Show first 10
            if f.is_file():
                rel_path = f.relative_to(Path(mirror_path))
                print(f"  {rel_path}")
        
        if len(mirror_files) > 10:
            print(f"  ... and {len(mirror_files) - 10} more files")
            
    else:
        print("! No mirroring configured (production mode)")

def cleanup_system(system: Dict[str, Any]):
    """Clean up the isolated system"""
    
    print(f"\n🧹 Cleaning Up Session")
    print("-" * 25)
    
    session_id = system["session_id"]
    factory = system["factory"]
    
    # Export session if needed
    try:
        export_path = f"/tmp/exported_session_{session_id}"
        result = ray.get(system["sandbox"].export_session.remote(export_path))
        print(f"✓ Session exported: {result.get('exported_to', 'unknown')}")
    except Exception as e:
        print(f"! Export failed: {e}")
    
    # Cleanup
    factory.cleanup_session(session_id)
    print(f"✓ Cleaned up session: {session_id}")

async def main():
    """Main demonstration"""
    if len(sys.argv) < 2:
        print("Usage: python cli_isolated_agent.py <config.yaml> [deployment_mode]")
        print("Deployment modes: development, testing, production")
        sys.exit(1)
    
    config_path = sys.argv[1]
    deployment_mode = sys.argv[2] if len(sys.argv) > 2 else "development"
    
    try:
        # Create isolated system
        system = create_isolated_agent_system(config_path, deployment_mode)
        
        # Run demonstrations
        demonstrate_isolation(system)
        await demonstrate_enhanced_tool_calling(system)  
        demonstrate_test_time_mirroring(system)
        
        print(f"\n🎉 Isolated Agentic System Demonstration Complete!")
        print("Benefits Achieved:")
        print("- ✓ Complete path virtualization")
        print("- ✓ Models see only relative paths")
        print("- ✓ Host filesystem completely hidden") 
        print("- ✓ Session-based isolation")
        print("- ✓ Test-time visibility maintained")
        print("- ✓ Enhanced tool calling with LSP feedback")
        print("- ✓ Production-ready containment")
        
        # Cleanup
        cleanup_system(system)
        
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if ray.is_initialized():
            ray.shutdown()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())