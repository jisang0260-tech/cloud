# CHAPTER07-2 실습 코드 정리

PDF에 나뉘어 있던 코드를 실행 단위로 다시 묶었습니다.
바로 복사해서 사용할 수 있도록 문법만 최소한으로 정리했습니다.

## 1. Fashion MNIST 불러오기

```python
from torchvision.datasets import FashionMNIST

fm_train = FashionMNIST(root=".", train=True, download=True)
fm_test = FashionMNIST(root=".", train=False, download=True)

print(type(fm_train.data))
print(fm_train.data.shape, fm_train.targets.shape)
print(fm_test.data.shape, fm_test.targets.shape)
```

## 2. 레이블 확인

```python
import torch

print(fm_train.targets[:10])

values, counts = torch.unique(fm_train.targets, return_counts=True)
print(values)
print(counts)

class_names = [
    "티셔츠",
    "바지",
    "스웨터",
    "드레스",
    "코트",
    "샌들",
    "셔츠",
    "스니커즈",
    "가방",
    "앵클부츠",
]
```

## 3. 샘플 이미지 출력

```python
import matplotlib.pyplot as plt

fig, axs = plt.subplots(1, 10, figsize=(10, 10))
for i in range(10):
    axs[i].imshow(fm_train.data[i], cmap="gray_r")
    axs[i].axis("off")

plt.show()
print([fm_train.targets[i].item() for i in range(10)])
```

## 4. 훈련 세트와 검증 세트 분리

```python
from sklearn.model_selection import train_test_split

train_input = fm_train.data
train_target = fm_train.targets

train_scaled = train_input / 255.0
train_scaled = train_scaled.reshape(-1, 28 * 28)

train_scaled, val_scaled, train_target, val_target = train_test_split(
    train_scaled, train_target, test_size=0.2, random_state=42
)

print(train_scaled.shape, train_target.shape)
print(val_scaled.shape, val_target.shape)
```

## 5. SGDClassifier 기준선 모델

```python
from sklearn.linear_model import SGDClassifier

sc = SGDClassifier(loss="log_loss", max_iter=5, random_state=42)
sc.fit(train_scaled, train_target)

print(sc.score(train_scaled, train_target))
print(sc.score(val_scaled, val_target))
```

## 6. 단층 신경망 모델 만들기

```python
import torch.nn as nn

model = nn.Sequential(
    nn.Linear(784, 10)
)

print(model)
for params in model.parameters():
    print(params.shape)
```

## 7. 단층 신경망 학습 전체 코드

```python
from torchvision.datasets import FashionMNIST
from sklearn.model_selection import train_test_split
import torch
import torch.nn as nn
import torch.optim as optim

fm_train = FashionMNIST(root=".", train=True, download=True)
fm_test = FashionMNIST(root=".", train=False, download=True)

train_input = fm_train.data
train_target = fm_train.targets

train_scaled = train_input / 255.0
train_scaled = train_scaled.reshape(-1, 28 * 28)

train_scaled, val_scaled, train_target, val_target = train_test_split(
    train_scaled, train_target, test_size=0.2, random_state=42
)

model = nn.Sequential(
    nn.Linear(784, 10)
)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)

criterion = nn.CrossEntropyLoss()
optimizer = optim.SGD(model.parameters(), lr=0.1)

epochs = 5
batch_size = 32
batches = int(len(train_scaled) / batch_size)

for epoch in range(epochs):
    model.train()
    train_loss = 0

    for i in range(batches):
        inputs = train_scaled[i * batch_size:(i + 1) * batch_size].to(device)
        targets = train_target[i * batch_size:(i + 1) * batch_size].to(device)

        optimizer.zero_grad()
        outputs = model(inputs)
        loss = criterion(outputs, targets)
        loss.backward()
        optimizer.step()

        train_loss += loss.item()

    print(f"에포크:{epoch + 1}, 손실:{train_loss / batches:.4f}")

model.eval()
with torch.no_grad():
    outputs = model(val_scaled.to(device))
    predicts = torch.argmax(outputs, dim=1)
    corrects = (predicts == val_target.to(device)).sum().item()
    accuracy = corrects / len(val_target)
    print(f"검증 정확도: {accuracy:.4f}")
```

## 8. 은닉층 추가: 시그모이드 버전

```python
import torch.nn as nn

model = nn.Sequential(
    nn.Linear(784, 100),
    nn.Sigmoid(),
    nn.Linear(100, 10)
)

print(model)
```

## 9. 모델 구조 요약 보기

```python
# 필요하면 먼저 설치
# !pip install torchinfo

from torchinfo import summary

summary(model, input_size=(32, 784))
```

## 10. add_module로 층 추가하기

```python
import torch.nn as nn

model = nn.Sequential()
model.add_module("dense1", nn.Linear(784, 100))
model.add_module("sigmoid", nn.Sigmoid())
model.add_module("dense2", nn.Linear(100, 10))

print(model)
```

## 11. ReLU 버전 모델

```python
import torch.nn as nn

model = nn.Sequential(
    nn.Flatten(),
    nn.Linear(784, 100),
    nn.ReLU(),
    nn.Linear(100, 10)
)

print(model)
```

## 12. ReLU 버전 학습 전체 코드

```python
from torchvision.datasets import FashionMNIST
from sklearn.model_selection import train_test_split
import torch
import torch.nn as nn
import torch.optim as optim

fm_train = FashionMNIST(root=".", train=True, download=True)
fm_test = FashionMNIST(root=".", train=False, download=True)

train_input = fm_train.data
train_target = fm_train.targets

train_scaled = train_input / 255.0

train_scaled, val_scaled, train_target, val_target = train_test_split(
    train_scaled, train_target, test_size=0.2, random_state=42
)

model = nn.Sequential(
    nn.Flatten(),
    nn.Linear(784, 100),
    nn.ReLU(),
    nn.Linear(100, 10)
)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)

criterion = nn.CrossEntropyLoss()
optimizer = optim.Adam(model.parameters())

for params in model.parameters():
    print(params.shape)

# 다른 옵티마이저 예시
# optimizer = optim.SGD(model.parameters(), lr=0.01)
# optimizer = optim.SGD(model.parameters(), lr=0.01, momentum=0.9, nesterov=True)
# optimizer = optim.Adagrad(model.parameters(), lr=0.01)
# optimizer = optim.RMSprop(model.parameters(), lr=0.001)
# optimizer = optim.Adam(model.parameters(), lr=0.001)

epochs = 5
batches = int(len(train_scaled) / 32)

for epoch in range(epochs):
    model.train()
    train_loss = 0

    for i in range(batches):
        inputs = train_scaled[i * 32:(i + 1) * 32].to(device)
        targets = train_target[i * 32:(i + 1) * 32].to(device)

        optimizer.zero_grad()
        outputs = model(inputs)
        loss = criterion(outputs, targets)
        loss.backward()
        optimizer.step()

        train_loss += loss.item()

    print(f"에포크:{epoch + 1}, 손실:{train_loss / batches:.4f}")

model.eval()
with torch.no_grad():
    val_scaled = val_scaled.to(device)
    val_target = val_target.to(device)
    outputs = model(val_scaled)
    predicts = torch.argmax(outputs, 1)
    corrects = (predicts == val_target).sum().item()
    accuracy = corrects / len(val_target)
    print(f"검증 정확도: {accuracy:.4f}")
```

## 13. 가장 복붙하기 쉬운 추천 순서

수업에서 바로 실행하려면 이 순서가 가장 편합니다.

1. `7. 단층 신경망 학습 전체 코드`
2. `8. 은닉층 추가: 시그모이드 버전`
3. `12. ReLU 버전 학습 전체 코드`

위 3개만 복사해서 써도 핵심 실습은 거의 그대로 따라갈 수 있습니다.
