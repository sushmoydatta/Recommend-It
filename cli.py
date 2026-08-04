"""
Command-line interface for Recommend-It.

Usage:
    python cli.py "Inception"
    python cli.py "The Dark Knight" --top 10
    python cli.py "Titanic" --data ./data/movies.csv
"""

import argparse
import sys

from recommender import MovieRecommender


def main():
    parser = argparse.ArgumentParser(description="Get content-based movie recommendations.")
    parser.add_argument("movie", help="Title of a movie you like")
    parser.add_argument("--top", type=int, default=5, help="Number of recommendations to return (default: 5)")
    parser.add_argument("--data", default="data/movies.csv", help="Path to the movies dataset CSV")
    args = parser.parse_args()

    try:
        engine = MovieRecommender(args.data)
        results = engine.recommend(args.movie, top_n=args.top)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nBecause you liked '{args.movie}', you might enjoy:\n")
    for i, rec in enumerate(results, start=1):
        print(f"  {i}. {rec.title}  (similarity: {rec.score})")
    print()


if __name__ == "__main__":
    main()
