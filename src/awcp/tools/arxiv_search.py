import arxiv
from awcp.runtime.tool_runtime import tool


@tool("search_arxiv")
def search_arxiv(
    query: str,
    max_results: int = 5
) -> list[dict]:
    """Search academic papers on arXiv for research and scientific publications.
    
    Use this tool for:
    - Research papers and scholarly articles
    - Scientific publications and academic content
    - Technical research on specific topics
    - Latest studies and academic findings
    
    Args:
        query: Search query for academic papers (e.g., "quantum computing", "machine learning transformers")
        max_results: Maximum number of papers to return (default: 5)
        
    Returns:
        List of papers with title, authors, summary, publication date, and PDF URL
    """

    try:

        client = arxiv.Client()

        search = arxiv.Search(
            query=query,
            max_results=max_results
        )

        papers = []

        for paper in client.results(search):

            papers.append(
                {
                    "title": paper.title,
                    "authors": [str(a) for a in paper.authors],
                    "summary": paper.summary,
                    "published": str(paper.published),
                    "pdf_url": paper.pdf_url,
                    "entry_id": paper.entry_id,
                }
            )

        return papers

    except Exception as e:

        raise RuntimeError(
            f"Arxiv search failed: {str(e)}"
        )
