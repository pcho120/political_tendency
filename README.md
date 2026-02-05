# Firm Website Finder

A Streamlit web application that automatically finds official websites for law firms from an Excel file.

## Features

- Upload Excel files containing a "Firm" column
- Automatically search for each firm's official website using DuckDuckGo
- Filter out unwanted results (Wikipedia, LinkedIn, rankings sites, etc.)
- Add results to a new "Official Website" column
- Download the updated Excel file
- Progress tracking and error handling
- Configurable search delays and row limits

## Requirements

- Python 3.7+
- Required packages (install with `pip install -r requirements.txt`):
  - streamlit
  - requests
  - beautifulsoup4
  - openpyxl
  - lxml

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Run the application:
   ```bash
   streamlit run name_to_URL.py
   ```
   
2. Open your browser and go to `http://localhost:8501`

3. Upload an Excel file (.xlsx) that contains a column named "Firm"

4. Configure settings:
   - **Delay between searches**: Adjust to avoid rate limiting (0.2-2.0 seconds)
   - **Max firms to process**: Limit for testing purposes

5. Click "🔍 Find Official Websites" to start processing

6. Wait for the process to complete. The app will show:
   - Progress bar
   - Current firm being searched
   - Summary of results
   - List of firms where no website was found

7. Download the updated Excel file with the "Official Website" column added

## How It Works

1. **Excel Processing**: The app reads the uploaded Excel file and finds the "Firm" column
2. **Web Searching**: For each firm, it searches DuckDuckGo HTML results for "[Firm Name] official website"
3. **URL Extraction**: It extracts real URLs from DuckDuckGo redirect links
4. **Filtering**: Blocks known non-official sources (Wikipedia, LinkedIn, law directories, etc.)
5. **Normalization**: Returns clean homepage URLs in the format `https://domain.com`
6. **Output**: Adds results to a new column and preserves all existing data

## Blocked Domains

The application automatically filters out results from these domains:
- wikipedia.org
- linkedin.com
- facebook.com
- instagram.com
- twitter.com
- law.com
- vault.com
- chambers.com
- bloomberg.com
- crunchbase.com

## File Requirements

- Excel file must be in .xlsx format
- Must contain a column named "Firm" (case-insensitive)
- The "Official Website" column will be added at the end of the header row
- Empty cells in the Firm column will be skipped

## Troubleshooting

- **No "Firm" column found**: Ensure your Excel file has a column with exactly "Firm" as the header
- **Rate limiting**: Increase the delay between searches if you encounter errors
- **Network issues**: Check your internet connection and try again
- **Empty results**: Some firms may not have official websites or may not be findable

## Example Output

| Firm | ... | Official Website |
|------|-----|------------------|
| Baker McKenzie | ... | https://bakermckenzie.com |
| Skadden Arps | ... | https://skadden.com |
| Jones Day | ... | https://jonesday.com |

## Notes

- The application runs locally and does not require any API keys
- Results are saved as direct homepage URLs (e.g., `https://example.com`)
- Processing time depends on the number of firms and search delay settings
- Always review results for accuracy, especially for important business use