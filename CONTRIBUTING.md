# Contributing Guidelines

> 📖 [한국어 문서](CONTRIBUTING_ko.md)

Thank you for contributing to the PRISM-INSIGHT project! 🎉

## Contributor License Agreement

PRISM-INSIGHT is offered under the AGPL-3.0 and under separate commercial
license terms. To keep that dual-licensing model legally clear, every pull
request contributor must accept the [Individual Contributor License
Agreement](CLA.md) before a pull request can be merged.

The CLA does not transfer ownership of your contribution. You keep your
copyright while granting the Project the rights needed to distribute your
contribution under both open-source and commercial terms.

The CLA Assistant will comment on a pull request when a signature is needed.
After reading the CLA, post the following exact comment on the pull request:

```text
I have read the CLA Document and I hereby sign the CLA
```

The signature is normally required once for each version of the CLA. If more
than one person authored a pull request, every author must sign. A contributor
must also have authority to submit the contribution; if an employer or another
party owns relevant rights, obtain the necessary authorization before signing.

## 📋 How to Contribute

### 1. Issue Reporting
Please report through GitHub Issues for:
- 🐛 **Bug Found**: When something doesn't work as expected
- 💡 **Feature Suggestion**: New feature ideas
- 📚 **Documentation Improvement**: Suggestions for improving README or code comments
- ❓ **Questions**: Inquiries about usage or configuration

### 2. Pull Request Submission

#### Basic Procedure
1. **Fork Project**: Click Fork button on GitHub
2. **Clone Locally**: `git clone https://github.com/dragon1086/prism-insight.git`
3. **Create Branch**: `git checkout -b feature/new-feature`
4. **Make Changes**: Modify code and test
5. **Commit**: `git commit -m "feat: Add new feature"`
6. **Push**: `git push origin feature/new-feature`
7. **Create Pull Request**: Create PR on GitHub

#### PR Submission Checklist
- [ ] Read and accept the [CLA](CLA.md)
- [ ] Verify code runs correctly
- [ ] Perform simple tests for new features
- [ ] Add appropriate code comments
- [ ] Update README (if necessary)

## 🔧 Development Environment Setup

### Basic Setup
```bash
# Clone repository
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight

# Install dependencies
pip install -r requirements.txt

# Prepare configuration files
cp .env.example .env
cp config.py.example config.py
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
```

### Test Environment
```bash
# Basic analysis test
python cores/main.py

# Individual module test
python trigger_batch.py morning INFO --output test_results.json
```

## 📝 Coding Rules

### Code Style
- **Language**: Python 3.10+ compatible
- **Indentation**: 4 spaces
- **Naming**:
  - Variables/Functions: `snake_case`
  - Classes: `PascalCase`
  - Constants: `UPPER_CASE`

### Comments and Documentation
```python
def analyze_stock(company_code: str, company_name: str, reference_date: str = None):
    """
    Generate comprehensive stock analysis report

    Args:
        company_code: Stock code (e.g., "005930")
        company_name: Company name (e.g., "Samsung Electronics")
        reference_date: Analysis reference date (YYYYMMDD format, default: today)

    Returns:
        str: Generated final report markdown text
    """
```

### Commit Message Rules
```bash
feat: Add new feature
fix: Fix bug
docs: Modify documentation
style: Code formatting (no functionality change)
refactor: Code refactoring
test: Add tests
chore: Build, configuration related
```

## 🎯 Contribution Areas

### High Priority
- 🤖 **AI Agent Improvement**: Enhance analysis performance
- 📊 **Trading Simulation**: Improve trading simulator returns
- 🐛 **Bug Fixes**: Improve stability
- 📚 **Documentation**: Improve usage guides

### Welcome Contributions
- 🏢 **Corporate Analysis Expansion**: New analysis sections or improve existing analysis
- 🌐 **Internationalization**: English support, overseas stock analysis, source code globalization
- 🚀 **Performance Optimization**: Improve analysis and trading simulator performance
- 🔧 **Configuration Improvement**: Easier configuration methods

### Precautions
- **API Key Related**: Do not include actual API keys in code
- **Large Files**: Do not commit generated reports or chart images
- **External Dependencies**: Discuss in issues first before adding new libraries

## 🚫 What Not to Do

- ❌ Include actual API keys or tokens in code
- ❌ Include personal information or sensitive data
- ❌ Code with potential copyright infringement
- ❌ Code that intentionally damages the system
- ❌ Investment solicitation or definitive investment advice

## 🔍 Code Review Process

### Review Criteria
1. **Functionality**: Does it work as intended?
2. **Stability**: Is error handling appropriate?
3. **Readability**: Is it easy for other developers to understand?
4. **Performance**: Is there unnecessary resource usage?
5. **Security**: Is there exposure of API keys or sensitive information?

### Review Time
- Generally reviewed within **1-7 days**
- Priority review within **24 hours** for urgent bug fixes

## 🐛 Bug Report

### Good Bug Report Example
```markdown
**Bug Description**
Analysis stops for specific stock during surge stock detection.

**Reproduction Steps**
1. Run `python stock_analysis_orchestrator.py --mode morning`
2. Error occurs when analyzing stock "123456"

**Expected Result**
Analysis completes normally

**Actual Result**
KeyError: 'current_price' error occurs

**Environment Information**
- OS: macOS 14.0
- Python: 3.9.7
- Error Log: [attached]
```

## 💡 Feature Suggestion

### What to Include in Suggestions
- **Background**: Why is this feature needed?
- **Feature Description**: What exactly is the feature?
- **Usage Example**: How will it be used?
- **Priority**: How important is it?

## 🎉 Contributor Recognition

All contributors are recognized as follows:
- **README.md** contributor list added
- **Release Notes** contribution details specified
- Reflected in **GitHub Contributions** graph

## 📞 Communication Channels

- **GitHub Issues**: Bugs, feature suggestions, questions
- **Pull Request**: Code review and discussion
- **Discussions**: General idea sharing

## ⚖️ Code of Conduct

- 🤝 **Respect**: Respect all contributors and provide constructive feedback
- 🌟 **Inclusion**: Welcome contributors with diverse backgrounds and experiences
- 📚 **Learning**: Accept mistakes as learning opportunities
- 🎯 **Focus**: Pursue contributions that align with project goals

---

**Thank you once again for contributing to PRISM-INSIGHT! 🚀**

If you have any questions, please feel free to contact us through GitHub Issues.
