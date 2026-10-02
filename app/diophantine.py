"""Exact integer solving of linear Diophantine systems ``A x = b``.

The solver relies on the Smith normal form ``D = U @ A @ V`` where ``U``
and ``V`` are unimodular integer matrices (products of invertible integer
row / column operations).  ``A x = b`` has an integer solution iff the
Smith pivots divide the corresponding entries of the transformed target
``U @ b`` and the transformed target vanishes on every zero row of ``D``.

All arithmetic uses Python arbitrary-precision integers.  No floating
point conversion and no rounding of rationals occurs anywhere in this
module; divisions are exact Euclidean divisions whose remainders are
kept explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence

Matrix = List[List[int]]


def _identity(size: int) -> Matrix:
    return [[1 if i == j else 0 for j in range(size)] for i in range(size)]


@dataclass
class SmithDecomposition:
    """Smith normal form data with ``D = U @ A @ V``."""

    d: Matrix  # m x n diagonal (Smith) matrix
    u: Matrix  # m x m unimodular: invertible integer row transforms
    v: Matrix  # n x n unimodular: invertible integer column transforms
    rank: int
    diagonal: List[int]  # positive pivots d_1 | d_2 | ... | d_rank


def smith_normal_form(matrix: Sequence[Sequence[int]]) -> SmithDecomposition:
    """Compute the Smith normal form of an integer matrix exactly."""
    a = [list(row) for row in matrix]
    m = len(a)
    n = len(a[0]) if m else 0
    u = _identity(m)
    v = _identity(n)

    def swap_rows(i: int, j: int) -> None:
        if i != j:
            a[i], a[j] = a[j], a[i]
            u[i], u[j] = u[j], u[i]

    def negate_row(i: int) -> None:
        a[i] = [-value for value in a[i]]
        u[i] = [-value for value in u[i]]

    def add_row_multiple(src: int, dst: int, factor: int) -> None:
        if factor:
            a[dst] = [x + factor * y for x, y in zip(a[dst], a[src])]
            u[dst] = [x + factor * y for x, y in zip(u[dst], u[src])]

    def swap_cols(i: int, j: int) -> None:
        if i != j:
            for row in a:
                row[i], row[j] = row[j], row[i]
            for row in v:
                row[i], row[j] = row[j], row[i]

    def add_col_multiple(src: int, dst: int, factor: int) -> None:
        if factor:
            for row in a:
                row[dst] += factor * row[src]
            for row in v:
                row[dst] += factor * row[src]

    rank = 0
    limit = min(m, n)
    while rank < limit:
        t = rank
        # Move the smallest nonzero entry of the trailing submatrix to (t, t).
        pivot = None
        for i in range(t, m):
            for j in range(t, n):
                if a[i][j] != 0 and (
                    pivot is None or abs(a[i][j]) < abs(a[pivot[0]][pivot[1]])
                ):
                    pivot = (i, j)
        if pivot is None:
            break
        swap_rows(t, pivot[0])
        swap_cols(t, pivot[1])

        while True:
            # Clear column t below the pivot (Euclidean reduction; every swap
            # strictly shrinks |a[t][t]|, so this terminates).
            i = t + 1
            while i < m:
                if a[i][t] != 0:
                    quotient = a[i][t] // a[t][t]
                    add_row_multiple(t, i, -quotient)
                    if a[i][t] != 0:
                        swap_rows(t, i)
                        continue
                i += 1
            # Clear row t to the right of the pivot.
            j = t + 1
            while j < n:
                if a[t][j] != 0:
                    quotient = a[t][j] // a[t][t]
                    add_col_multiple(t, j, -quotient)
                    if a[t][j] != 0:
                        swap_cols(t, j)
                        continue
                j += 1
            # Row clearing may have dirtied the column and vice versa.
            if any(a[i][t] != 0 for i in range(t + 1, m)):
                continue
            if any(a[t][j] != 0 for j in range(t + 1, n)):
                continue
            # The pivot must divide the whole trailing submatrix; otherwise
            # pull an offending entry into row t and keep reducing.
            bad = None
            for i in range(t + 1, m):
                for j in range(t + 1, n):
                    if a[i][j] % a[t][t] != 0:
                        bad = (i, j)
                        break
                if bad is not None:
                    break
            if bad is None:
                break
            add_row_multiple(bad[0], t, 1)

        if a[t][t] < 0:
            negate_row(t)
        rank += 1

    diagonal = [a[i][i] for i in range(rank)]
    return SmithDecomposition(d=a, u=u, v=v, rank=rank, diagonal=diagonal)


@dataclass
class Obstruction:
    """Canonical divisibility obstruction to integer solvability.

    Derived purely from invertible integer row transformations: the
    transformed target is ``U @ b`` and the pivot is the Smith diagonal
    entry of the same row.
    """

    kind: str  # "non_divisible" | "zero_row"
    row: int  # row index in the Smith-transformed system
    pivot: int  # Smith pivot of that row (0 for a zero row)
    transformed_target: int  # (U @ b)[row]
    remainder: int  # transformed_target mod pivot (target itself for zero rows)
    u_row: List[int]  # row of U producing the transformed target


@dataclass
class SolveResult:
    solvable: bool
    solution: List[int] = field(default_factory=list)
    obstruction: Obstruction | None = None
    smith: SmithDecomposition | None = None
    transformed_target: List[int] = field(default_factory=list)  # U @ b
    homogeneous_basis: List[List[int]] = field(default_factory=list)


def solve_diophantine(
    matrix: Sequence[Sequence[int]], target: Sequence[int]
) -> SolveResult:
    """Decide integer solvability of ``matrix @ x = target`` exactly.

    Returns a particular integer solution (free variables set to zero)
    plus a basis of the homogeneous solution space when solvable, or the
    canonical divisibility obstruction when not.
    """
    m = len(matrix)
    n = len(matrix[0]) if m else 0
    if m == 0 or n == 0:
        raise ValueError("约束矩阵不能为空")
    if len(target) != m:
        raise ValueError("目标向量长度必须与约束条数一致")
    if any(len(row) != n for row in matrix):
        raise ValueError("约束矩阵各行长度必须一致")

    smith = smith_normal_form(matrix)
    b_transformed = [
        sum(smith.u[i][k] * target[k] for k in range(m)) for i in range(m)
    ]

    for i in range(smith.rank):
        pivot = smith.diagonal[i]
        remainder = b_transformed[i] % pivot
        if remainder != 0:
            return SolveResult(
                solvable=False,
                obstruction=Obstruction(
                    kind="non_divisible",
                    row=i,
                    pivot=pivot,
                    transformed_target=b_transformed[i],
                    remainder=remainder,
                    u_row=list(smith.u[i]),
                ),
                smith=smith,
                transformed_target=b_transformed,
            )
    for i in range(smith.rank, m):
        if b_transformed[i] != 0:
            return SolveResult(
                solvable=False,
                obstruction=Obstruction(
                    kind="zero_row",
                    row=i,
                    pivot=0,
                    transformed_target=b_transformed[i],
                    remainder=b_transformed[i],
                    u_row=list(smith.u[i]),
                ),
                smith=smith,
                transformed_target=b_transformed,
            )

    y = [b_transformed[i] // smith.diagonal[i] for i in range(smith.rank)]
    y.extend([0] * (n - smith.rank))
    solution = [sum(smith.v[i][j] * y[j] for j in range(n)) for i in range(n)]

    # Exact self-check: the candidate solution must reproduce the target.
    for i in range(m):
        if sum(matrix[i][j] * solution[j] for j in range(n)) != target[i]:
            raise ArithmeticError("内部校验失败：解未复现目标向量")

    basis = [
        [smith.v[i][j] for i in range(n)] for j in range(smith.rank, n)
    ]
    return SolveResult(
        solvable=True,
        solution=solution,
        smith=smith,
        transformed_target=b_transformed,
        homogeneous_basis=basis,
    )
