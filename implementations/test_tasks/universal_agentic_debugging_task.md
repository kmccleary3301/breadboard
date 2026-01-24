# Universal Agentic Coding System Evaluation Task

## Primary Objective
Create a complete prototype filesystem implementation in C that compiles with GCC and passes comprehensive tests. This task is designed to exercise the full capabilities of agentic coding systems while producing a meaningful technical deliverable.

## Core Requirements

### 1. Filesystem Implementation
Create a self-contained C filesystem library with these specifications:
- **Storage**: Single binary file `filesystem.fs` containing all data and metadata
- **Core Operations**: 
  - `fs_create()` - Initialize new filesystem
  - `fs_mount()` / `fs_unmount()` - Mount/unmount operations
  - `fs_write(path, data, size)` - Write file data
  - `fs_read(path, buffer, size)` - Read file data  
  - `fs_list(path)` - List directory contents
  - `fs_rename(old_path, new_path)` - Rename files/directories
  - `fs_delete(path)` - Delete files/directories
  - `fs_mkdir(path)` - Create directories
- **Headers**: Proper `.h` header file with function declarations
- **Error Handling**: Return codes for all error conditions
- **Memory Management**: No memory leaks, proper cleanup

### 2. Testing & Validation
- **Test Suite**: Comprehensive C test program that validates all operations
- **Compilation**: Must compile cleanly with `gcc -Wall -Wextra -Werror`
- **Execution**: Tests must run and pass completely
- **Edge Cases**: Test error conditions, boundary cases, invalid inputs
- **Stress Testing**: Create/delete many files, large file operations

### 3. Development Process Requirements
You must demonstrate thorough engineering practices by:

#### A. Exploration Phase
- **Research existing implementations**: Search for similar filesystem code, examine patterns
- **Analyze project structure**: Understand the current codebase layout and conventions
- **Review relevant documentation**: Look for any existing filesystem or C development guidelines

#### B. Implementation Phase  
- **Incremental development**: Build in logical stages (data structures → basic ops → advanced ops)
- **Continuous validation**: Compile and test after each major addition
- **Code quality checks**: Use compiler warnings, static analysis if available
- **Documentation**: Comment complex algorithms and data structures

#### C. Testing & Refinement Phase
- **Multi-stage testing**: Unit tests → integration tests → stress tests
- **Error reproduction**: Deliberately trigger errors to test error handling
- **Performance validation**: Test with various file sizes and quantities  
- **Code review**: Re-examine implementation for improvements

#### D. Demonstration Phase
- **Working examples**: Create sample programs showing filesystem usage
- **Performance benchmarks**: Measure operation speeds, memory usage
- **Robustness testing**: Test recovery from corrupted filesystems
- **Documentation artifacts**: Generate usage examples and API documentation

## Critical Success Criteria

### Technical Requirements
1. **Compiles successfully** with zero warnings using `gcc -Wall -Wextra -Werror`
2. **All tests pass** including edge cases and error scenarios  
3. **No memory leaks** when run with valgrind or similar tools
4. **Self-contained** - single binary file stores complete filesystem
5. **Cross-platform compatible** - works on standard Unix-like systems

### Process Requirements
1. **Tool diversity**: Use every available tool in your toolkit at least once during the process
2. **Iterative refinement**: Show multiple compile-test-fix cycles
3. **Comprehensive exploration**: Search existing code, read documentation, examine similar implementations
4. **Error handling**: Demonstrate graceful handling of compilation errors, test failures, and runtime issues
5. **Documentation**: Create clear documentation of the implementation approach and API usage

## Evaluation Focus Areas

This task is designed to observe and analyze:

### Tool Usage Patterns
- **File Operations**: How you create, read, edit, and manage multiple source files
- **Search Capabilities**: How you explore codebases, find examples, and research solutions
- **Command Execution**: How you use shell commands for compilation, testing, and debugging
- **Error Diagnosis**: How you handle and resolve compilation errors, test failures, and runtime issues
- **Documentation**: How you create and maintain project documentation

### Problem-Solving Approaches
- **Planning**: Do you plan the entire implementation or work incrementally?
- **Tool Selection**: Which tools do you choose for different subtasks?
- **Error Recovery**: How do you handle and resolve problems?
- **Quality Assurance**: What validation and testing strategies do you employ?
- **Optimization**: Do you refactor and improve initial implementations?

### Technical Depth
- **Algorithm Design**: How do you design the filesystem data structures and algorithms?
- **Memory Management**: How do you handle dynamic memory allocation and cleanup?
- **Error Handling**: How thoroughly do you implement error checking and recovery?
- **Testing Strategy**: How comprehensive are your test cases and validation approaches?
- **Code Quality**: How clean, readable, and maintainable is your implementation?

## Expected Deliverables

Upon completion, the following files should exist and be fully functional:

1. **`protofilsystem.h`** - Header file with all function declarations and data structures
2. **`protofilesystem.c`** - Complete implementation of filesystem operations  
3. **`test_filesystem.c`** - Comprehensive test suite covering all functionality
4. **`Makefile`** - Build system for compiling library and tests
5. **`README.md`** - Documentation explaining usage, compilation, and design decisions
6. **`demo.c`** - Example program demonstrating filesystem usage
7. **`filesystem.fs`** - Sample filesystem file created during testing

## Execution Strategy

Execute this task with maximum tool utilization and engineering rigor. This is not just about building a filesystem - it's about demonstrating the full capabilities of your agentic coding environment. Use every tool at your disposal, explore thoroughly, test comprehensively, and refine continuously.

The goal is to produce not only a working filesystem but also a rich trace of sophisticated agentic coding behavior that showcases the full potential of AI-assisted software development.

**Note**: This task serves dual purposes - creating a meaningful technical artifact while providing comprehensive insights into agentic coding capabilities, tool usage patterns, and problem-solving approaches. Approach it as both an engineering challenge and a demonstration of your full technical capabilities.