"""Unit tests for the exact Diophantine solver."""

import random
import unittest

from app.diophantine import smith_normal_form, solve_diophantine


def matmul(a, b):
    return [
        [sum(x * y for x, y in zip(row_a, col_b)) for col_b in zip(*b)]
        for row_a in a
    ]


def bareiss_det(matrix):
    """Exact fraction-free determinant (Bareiss algorithm)."""
    a = [list(row) for row in matrix]
    n = len(a)
    if n == 0:
        return 1
    sign = 1
    prev = 1
    for k in range(n - 1):
        if a[k][k] == 0:
            pivot = next((i for i in range(k + 1, n) if a[i][k] != 0), None)
            if pivot is None:
                return 0
            a[k], a[pivot] = a[pivot], a[k]
            sign = -sign
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                a[i][j] = (a[i][j] * a[k][k] - a[i][k] * a[k][j]) // prev
        prev = a[k][k]
        for i in range(k + 1, n):
            a[i][k] = 0
    return sign * a[n - 1][n - 1]


class SmithNormalFormTest(unittest.TestCase):
    def test_identity(self):
        snf = smith_normal_form([[1, 0], [0, 1]])
        self.assertEqual(snf.diagonal, [1, 1])
        self.assertEqual(snf.rank, 2)

    def test_zero_matrix(self):
        snf = smith_normal_form([[0, 0], [0, 0]])
        self.assertEqual(snf.rank, 0)
        self.assertEqual(snf.diagonal, [])

    def test_single_row_gcd(self):
        snf = smith_normal_form([[6, 10, 15]])
        self.assertEqual(snf.diagonal, [1])

    def test_single_column(self):
        snf = smith_normal_form([[4], [6], [10]])
        self.assertEqual(snf.diagonal, [2])

    def test_diagonal_chain(self):
        snf = smith_normal_form([[2, 0], [0, 3]])
        self.assertEqual(snf.diagonal, [1, 6])

    def test_big_integers(self):
        big = 2**70 + 12345
        snf = smith_normal_form([[2 * big, big], [big, 3 * big]])
        self.assertEqual(snf.diagonal, [big, 5 * big])

    def test_random_small_matrices(self):
        rng = random.Random(20260925)
        for _ in range(60):
            m = rng.randint(1, 5)
            n = rng.randint(1, 5)
            a = [[rng.randint(-9, 9) for _ in range(n)] for _ in range(m)]
            snf = smith_normal_form(a)
            # D == U @ A @ V
            self.assertEqual(matmul(matmul(snf.u, a), snf.v), snf.d)
            # diagonal shape and divisibility chain
            for i in range(snf.rank):
                self.assertGreater(snf.d[i][i], 0)
                self.assertEqual(snf.d[i][i], snf.diagonal[i])
                if i + 1 < snf.rank:
                    self.assertEqual(snf.d[i + 1][i + 1] % snf.d[i][i], 0)
            for i in range(m):
                for j in range(n):
                    if i != j or i >= snf.rank:
                        self.assertEqual(snf.d[i][j], 0)
            # U and V are unimodular (invertible over the integers)
            self.assertEqual(abs(bareiss_det(snf.u)), 1)
            self.assertEqual(abs(bareiss_det(snf.v)), 1)


class SolveDiophantineTest(unittest.TestCase):
    def test_unique_solution_with_big_coefficients(self):
        matrix = [[9007199254740993, 1, 0], [1, 3, 1], [0, 2, 4]]
        target = [18014398509481989, 12, 10]
        result = solve_diophantine(matrix, target)
        self.assertTrue(result.solvable)
        self.assertEqual(result.solution, [2, 3, 1])

    def test_non_divisible_obstruction(self):
        result = solve_diophantine([[2, 0], [0, 2]], [3, 4])
        self.assertFalse(result.solvable)
        obstruction = result.obstruction
        self.assertEqual(obstruction.kind, "non_divisible")
        self.assertEqual(obstruction.pivot, 2)
        self.assertEqual(obstruction.transformed_target, 3)
        self.assertEqual(obstruction.remainder, 1)

    def test_zero_row_obstruction(self):
        result = solve_diophantine([[1, 1], [2, 2]], [1, 3])
        self.assertFalse(result.solvable)
        self.assertEqual(result.obstruction.kind, "zero_row")
        self.assertNotEqual(result.obstruction.transformed_target, 0)

    def test_big_exact_solution(self):
        result = solve_diophantine([[2**62]], [2**63])
        self.assertTrue(result.solvable)
        self.assertEqual(result.solution, [2])

    def test_big_non_divisible(self):
        result = solve_diophantine([[2**62]], [2**62 + 1])
        self.assertFalse(result.solvable)
        self.assertEqual(result.obstruction.kind, "non_divisible")
        self.assertEqual(result.obstruction.pivot, 2**62)
        self.assertEqual(result.obstruction.remainder, 1)

    def test_underdetermined_gcd_case(self):
        # 2x + 3y = 1 is solvable because gcd(2, 3) = 1; a naive
        # pivot-divides-target check on the row echelon form would
        # wrongly reject it.
        result = solve_diophantine([[2, 3]], [1])
        self.assertTrue(result.solvable)
        x, y = result.solution
        self.assertEqual(2 * x + 3 * y, 1)

    def test_random_consistent_systems(self):
        rng = random.Random(7)
        for _ in range(50):
            m = rng.randint(1, 5)
            n = rng.randint(1, 5)
            a = [[rng.randint(-8, 8) for _ in range(n)] for _ in range(m)]
            x0 = [rng.randint(-5, 5) for _ in range(n)]
            b = [sum(aij * xj for aij, xj in zip(row, x0)) for row in a]
            result = solve_diophantine(a, b)
            self.assertTrue(result.solvable)
            reproduced = [
                sum(aij * xj for aij, xj in zip(row, result.solution)) for row in a
            ]
            self.assertEqual(reproduced, b)
            for vector in result.homogeneous_basis:
                self.assertEqual(
                    [sum(aij * vj for aij, vj in zip(row, vector)) for row in a],
                    [0] * m,
                )

    def test_u_row_reconstructs_transformed_target(self):
        matrix = [[4, 2], [2, 4]]
        target = [1, 1]
        result = solve_diophantine(matrix, target)
        self.assertFalse(result.solvable)
        obstruction = result.obstruction
        reconstructed = sum(
            coefficient * value
            for coefficient, value in zip(obstruction.u_row, target)
        )
        self.assertEqual(reconstructed, obstruction.transformed_target)


if __name__ == "__main__":
    unittest.main()
