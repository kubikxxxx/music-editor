import { useState } from "react";

function App() {
    const [count, setCount] = useState(0);

    return (
        <div>
            <h1>Hudební editor</h1>
            <p>Počet kliknutí: {count}</p>
            <button onClick={() => setCount(count + 1)}>Klikni</button>
        </div>
    );
}

export default App;
