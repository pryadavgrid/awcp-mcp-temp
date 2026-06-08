import arxiv
from awcp.runtime.tool_runtime import tool


@tool("search_arxiv")
def search_arxiv(
    query: str,
    max_results: int = 5
) -> list[dict]:

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
