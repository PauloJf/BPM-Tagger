import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { ApiError } from "../lib/api";
import Login from "./Login";

const h = vi.hoisted(() => ({ login: vi.fn() }));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ state: null }),
}));

vi.mock("../lib/auth", () => ({ useAuth: () => ({ login: h.login }) }));

const pwField = () => screen.getByLabelText("Password") as HTMLInputElement;
const userField = () => screen.getByLabelText(/^Username/) as HTMLInputElement;

/** Sign in far enough that the server asks for a second factor. */
async function reachCodeStep() {
  h.login.mockRejectedValueOnce(new ApiError(401, "totp_required"));
  render(<Login />);
  fireEvent.change(pwField(), { target: { value: "s3cret" } });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
  await screen.findByLabelText(/authentication code/i);
}

describe("Login — two-factor step", () => {
  beforeEach(() => {
    cleanup();
    h.login.mockReset();
  });

  it("asks for a code when the server answers totp_required", async () => {
    await reachCodeStep();
    expect(screen.getByRole("button", { name: /verify/i })).toBeTruthy();
  });

  it("keeps the credential fields mounted alongside the code field", async () => {
    // Load-bearing for password-manager autofill: a manager fills a
    // one-time-code field only as part of filling a LOGIN form, so the code
    // input must sit in the same form as username + password rather than
    // replacing them. Swapping the form out again would silently break
    // Bitwarden's TOTP fill, hence this test.
    await reachCodeStep();
    const code = screen.getByLabelText(/authentication code/i) as HTMLInputElement;
    expect(code.getAttribute("autocomplete")).toBe("one-time-code");
    expect(userField().getAttribute("autocomplete")).toBe("username");
    expect(pwField().getAttribute("autocomplete")).toBe("current-password");
    // ...and all three in one form, which is what makes it a fillable login.
    expect(code.form).toBe(pwField().form);
    expect(code.form).toBe(userField().form);
    // The typed password survives the step change (it is resubmitted with the code).
    expect(pwField().value).toBe("s3cret");
  });

  it("submits password + code together, then goes back on a bad code", async () => {
    await reachCodeStep();
    h.login.mockRejectedValueOnce(new ApiError(401, "totp_invalid"));
    fireEvent.change(screen.getByLabelText(/authentication code/i), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: /verify/i }));
    await waitFor(() => expect(h.login).toHaveBeenLastCalledWith("s3cret", "", "123456"));
    // A wrong code keeps the user on the code step, cleared and flagged.
    const code = await screen.findByLabelText(/authentication code/i);
    expect((code as HTMLInputElement).value).toBe("");
    expect(screen.getByText(/invalid code/i)).toBeTruthy();
  });

  it("Back returns to the plain sign-in step", async () => {
    await reachCodeStep();
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(screen.queryByLabelText(/authentication code/i)).toBeNull();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeTruthy();
  });
});
